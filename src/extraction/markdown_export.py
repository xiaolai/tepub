from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

import html2text
from lxml import html as lxml_html
from rich.markup import escape

from config import AppSettings
from console_singleton import get_console
from epub_io.container import iter_toc
from epub_io.path_utils import normalize_epub_href
from epub_io.reader import EpubReader
from extraction.segments import locate_units
from state.models import ExtractMode, Segment
from state.store import load_segments

console = get_console()


@dataclass
class ChapterBlock:
    """Represents a logical chapter grouping multiple spine files."""

    title: str  # TOC entry title
    spine_start: int  # First spine index in this block
    spine_end: int  # Last spine index (exclusive) - start of next block
    files: list[Path]  # All file paths in this block, sorted by spine
    toc_href: str  # Original TOC href for reference




TocEntry = tuple[str, int, str]  # (title, spine_index, href)


def _build_spine_lookup(reader: EpubReader) -> dict[Path, int]:
    """Map each spine document path to its spine index."""
    return {item.href: item.index for item in reader.package.spine_items()}


def _collect_toc_entries(toc, spine_lookup: dict[Path, int]) -> list[TocEntry]:
    """Flatten the (possibly nested) TOC into entries that resolve to spine files."""
    entries: list[TocEntry] = []

    for node in iter_toc(toc):
        if not node.href:
            continue
        href = node.href.split("#", 1)[0]
        href_path = Path(href)
        if href_path in spine_lookup:
            entries.append((node.title or href, spine_lookup[href_path], href))
    return entries


def _dedupe_entries_by_spine_index(entries: list[TocEntry]) -> list[TocEntry]:
    """Keep the first TOC entry per spine index.

    Fragment hrefs ("ch1.xhtml#part-2") are stripped to the file, so several TOC
    entries can share one spine index. Left as-is they produce zero-length ranges
    [n, n): every such entry but the last contributes no files, and the last
    silently absorbs the whole file. The first entry is the one whose title
    introduces that file.
    """
    deduped: list[TocEntry] = []
    seen: set[int] = set()
    for entry in entries:
        if entry[1] in seen:
            continue
        seen.add(entry[1])
        deduped.append(entry)
    return deduped


def _files_with_spine_index(
    segments_by_file: dict[Path, list[Segment]],
) -> list[tuple[Path, int]]:
    """Every file that has segments, paired with its spine index, spine-ordered."""
    pairs = [
        (file_path, segments[0].metadata.spine_index)
        for file_path, segments in segments_by_file.items()
        if segments
    ]
    pairs.sort(key=lambda item: item[1])
    return pairs


def _single_file_block(file_path: Path, spine_idx: int) -> ChapterBlock:
    """A one-file block titled from the filename (front matter / TOC-less fallback)."""
    return ChapterBlock(
        title=file_path.stem.replace("_", " ").title(),
        spine_start=spine_idx,
        spine_end=spine_idx + 1,
        files=[file_path],
        toc_href=file_path.as_posix(),
    )


def _blocks_from_toc_ranges(
    toc_entries: list[TocEntry],
    segments_by_file: dict[Path, list[Segment]],
    max_spine_index: int,
) -> list[ChapterBlock]:
    """Build one block per TOC entry, spanning [this entry, next entry)."""
    files = _files_with_spine_index(segments_by_file)
    blocks: list[ChapterBlock] = []

    for i, (title, spine_start, href) in enumerate(toc_entries):
        spine_end = (
            toc_entries[i + 1][1] if i + 1 < len(toc_entries) else max_spine_index + 1
        )
        block_files = [
            file_path for file_path, idx in files if spine_start <= idx < spine_end
        ]
        if block_files:
            blocks.append(
                ChapterBlock(
                    title=title,
                    spine_start=spine_start,
                    spine_end=spine_end,
                    files=block_files,
                    toc_href=href,
                )
            )
    return blocks


def _front_matter_blocks(
    toc_entries: list[TocEntry],
    segments_by_file: dict[Path, list[Segment]],
) -> list[ChapterBlock]:
    """One block per file appearing before the first TOC entry."""
    if not toc_entries or not segments_by_file:
        return []
    first_toc_spine = min(entry[1] for entry in toc_entries)
    return [
        _single_file_block(file_path, idx)
        for file_path, idx in _files_with_spine_index(segments_by_file)
        if idx < first_toc_spine
    ]


def _spine_ordered_blocks(
    segments_by_file: dict[Path, list[Segment]],
) -> list[ChapterBlock]:
    """One block per file in spine order, used when the TOC yields nothing."""
    return [
        _single_file_block(file_path, idx)
        for file_path, idx in _files_with_spine_index(segments_by_file)
    ]


def _build_chapter_blocks(
    reader: EpubReader, segments_by_file: dict[Path, list[Segment]]
) -> list[ChapterBlock]:
    """
    Group spine files into chapter blocks based on TOC structure.

    Smart grouping logic:
    - If multiple spine files exist between two TOC entries, group them
    - If one TOC entry = one spine file, keep as single block
    - Files before first TOC entry are kept as individual blocks
    - Files after last TOC entry are grouped as one block

    Args:
        reader: EPUB reader with access to TOC and spine
        segments_by_file: Segments grouped by file path

    Returns:
        List of ChapterBlock objects, one per logical chapter/section
    """
    spine_lookup = _build_spine_lookup(reader)
    max_spine_index = max(spine_lookup.values()) if spine_lookup else 0

    toc_entries = _collect_toc_entries(reader.package.toc or [], spine_lookup)
    toc_entries.sort(key=lambda x: x[1])
    toc_entries = _dedupe_entries_by_spine_index(toc_entries)

    blocks = _blocks_from_toc_ranges(toc_entries, segments_by_file, max_spine_index)
    blocks = _front_matter_blocks(toc_entries, segments_by_file) + blocks

    # Fall back to spine order when the TOC is missing or none of its entries
    # matched the spine. Without this an EPUB with an unusable TOC exported
    # nothing at all: per-chapter export wrote no files and combined export
    # omitted every segment.
    if not blocks:
        blocks = _spine_ordered_blocks(segments_by_file)

    # Sort all blocks by spine index
    blocks.sort(key=lambda b: b.spine_start)

    return blocks


def _sanitize_filename(title: str, max_length: int = 50) -> str:
    """Convert title to safe filename component."""
    # Remove or replace unsafe characters
    safe = re.sub(r'[<>:"/\\|?*!]', "", title)
    safe = re.sub(r"\s+", "-", safe.strip())
    safe = safe.lower()
    # Remove leading/trailing hyphens
    safe = safe.strip("-")
    # Limit length
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip("-")
    return safe or "untitled"




def _image_source(element) -> str | None:
    return (
        element.get("src")
        or element.get("href")
        or element.get("xlink:href")
        or element.get("{http://www.w3.org/1999/xlink}href")
    )


def _prepare_markup(
    html_content: str,
    document_path: Path,
    image_mapping: dict[str, str],
    document_destinations: dict[str, str] | None = None,
) -> str:
    """Point images and document links at their exported files before conversion.

    Two image fixes had to happen afterwards and could not: html2text drops SVG
    <image> elements, so the reference was gone before a string replacement
    could use it; and inserting the extracted filename after conversion left
    spaces, "#" and parentheses to break the destination. Here <image> becomes
    <img> and every destination is percent-encoded, before html2text sees it.

    A link to another XHTML document named a file the export does not create;
    it now names the Markdown file that contains that document.
    """
    tree = lxml_html.fromstring(f"<div>{html_content}</div>")
    changed = False
    for element in tree.xpath(".//img | .//image"):
        src = _image_source(element)
        if not src:
            continue
        destination = src
        # Resolve against the *file* the reference points at: image_mapping is
        # keyed by clean EPUB paths, so "fig.png?v=2" or "icons.svg#star" never
        # matched and the image silently kept its original path. Strip the
        # query/fragment and percent-decode here rather than in
        # normalize_epub_href, which deliberately preserves both for document
        # links (see tests/epub_io/test_path_utils.py).
        lookup_src = unquote(src.split("#", 1)[0].split("?", 1)[0])
        normalized_path = normalize_epub_href(document_path, lookup_src)
        if normalized_path and normalized_path in image_mapping:
            destination = quote(f"images/{image_mapping[normalized_path]}")
        if element.tag == "image":
            # SVG carries the same reference; html2text keeps an <img>, not an
            # <image>.
            alt = element.get("alt") or element.get("title")
            element.tag = "img"
            for name in list(element.attrib):
                del element.attrib[name]
            if alt:
                element.set("alt", alt)
            changed = True
        elif destination != src:
            changed = True
        element.set("src", destination)

    if document_destinations:
        for link in tree.xpath(".//a[@href]"):
            href = link.get("href")
            document_part, marker, fragment = href.partition("#")
            if not document_part:
                continue  # a same-document fragment stays in this file
            normalized = normalize_epub_href(document_path, unquote(document_part))
            if not normalized or normalized not in document_destinations:
                continue  # external, or a file this export does not write
            destination = document_destinations[normalized]
            if destination.startswith("#") and fragment:
                # Everything in this export is one file, and the target id has
                # its own anchor here; jumping to the chapter top would lose it.
                link.set("href", f"#{fragment}")
            else:
                link.set("href", f"{destination}{marker}{fragment}")
            changed = True

    return lxml_html.tostring(tree, encoding="unicode") if changed else html_content


def _anchors(html_content: str) -> str:
    """Raw HTML anchors for the ids in a segment, so links can land on them.

    Markdown has no syntax for an id, and html2text drops the attribute.
    """
    tree = lxml_html.fromstring(f"<div>{html_content}</div>")
    return "".join(
        f'<a id="{html.escape(value, quote=True)}"></a>\n'
        for value in tree.xpath(".//*[@id]/@id")
    )


def _html_to_markdown(
    html_content: str,
    document_path: Path,
    image_mapping: dict[str, str],
    document_destinations: dict[str, str] | None = None,
    plain_text: bool = False,
) -> str:
    """
    Convert HTML content to markdown, preserving formatting and images.

    Args:
        html_content: HTML content to convert
        document_path: Path of the document containing this content (for resolving image refs)
        image_mapping: Mapping from EPUB image paths to extracted filenames
        document_destinations: Mapping from EPUB document paths to the Markdown
            file that contains them, for rewriting local links
        plain_text: The content is plain text escaped for HTML, not markup.
            Literal "<", ">" and "&" are then left for the caller to escape
            for Markdown with the rest of the text.

    Returns:
        Markdown formatted text with image references
    """
    try:
        # Configure html2text
        h = _MarkdownHTML2Text(literal_markup=not plain_text)
        h.body_width = 0  # Don't wrap lines
        h.ignore_links = False  # Preserve links
        h.skip_internal_links = False  # Preserve same-file fragment links too
        h.ignore_images = False  # Preserve images
        h.ignore_emphasis = False  # Preserve bold/italic
        h.mark_code = True  # Mark code blocks
        h.protect_links = True  # Don't alter link text
        h.single_line_break = False  # Use double line breaks for paragraphs

        # Convert HTML to markdown
        prepared = _prepare_markup(
            html_content, document_path, image_mapping, document_destinations
        )
        markdown = h.handle(prepared)

        return f"{_anchors(prepared)}{markdown.strip()}"
    except Exception as e:
        # Fall back to parser-based text extraction. The previous regex fallback
        # (`<[^>]+>` -> "") deleted any legitimate text containing angle brackets
        # and left HTML entities unresolved, silently corrupting the export.
        console.print(
            f"[yellow]Warning: HTML to markdown conversion failed: {escape(str(e))}[/yellow]"
        )
        try:
            fallback_tree = lxml_html.fromstring(f"<div>{html_content}</div>")
            return fallback_tree.text_content().strip()
        except Exception:
            # Content is not parseable as HTML at all; return it unchanged rather
            # than mangling it with a regex.
            return html_content.strip()


def _prepare_export(
    settings: AppSettings,
    input_epub: Path,
    image_mapping: dict[str, str] | None,
) -> tuple[
    dict[Path, list[Segment]],
    list[ChapterBlock],
    dict[str, str],
    _DocumentContext,
]:
    """Load segments, group them by file, and build chapter blocks.

    Shared by both exporters, which previously carried identical copies of this
    setup and could drift apart. Also returns each unit's document context: the
    unit stores only the inside of its block, so its own id (a note definition,
    a section target) and the list or table it was split out of are not in its
    source and would otherwise be lost.
    """
    segments_doc = load_segments(settings.segments_file)
    reader = EpubReader(input_epub, settings)
    img_map = image_mapping or {}

    # Group segments by file path
    by_file: dict[Path, list[Segment]] = {}
    for segment in segments_doc.segments:
        by_file.setdefault(segment.file_path, []).append(segment)

    # Sort segments within each file
    for file_path in by_file:
        by_file[file_path].sort(key=lambda s: s.metadata.order_in_file)

    blocks = _build_chapter_blocks(reader, by_file)
    return by_file, blocks, img_map, _document_context(reader, by_file)


#: Containers whose items become separate units once they are split.
_LIST_TAGS = frozenset({"ol", "ul", "dl"})
_ITEM_TAGS = frozenset({"li", "dt", "dd"})
_CELL_TAGS = frozenset({"td", "th"})


@dataclass(frozen=True)
class _Container:
    """A split list or table, rebuilt from the units it was split into."""

    key: tuple[Path, int]
    kind: str  # "list" or "table"
    open_tag: str
    close_tag: str
    #: Where this container itself sits when it is nested in another split
    #: container: the item or cell that must hold it, not follow it.
    parent: _ContainerRef | None = None


@dataclass(frozen=True)
class _ContainerRef:
    """Where a unit sits inside a split list or table."""

    key: tuple[Path, int]
    row: int = 0
    # Cell ordinal within the row; paragraph units of one cell share it.
    col: int = 0
    header: bool = False
    cell_tag: str = "td"
    cell_attrs: str = ""
    item_tag: str = "li"
    item_attrs: str = ""


@dataclass(frozen=True)
class _UnitContext:
    """What the document says about a unit's place, which its source cannot."""

    anchor: str | None = None
    # Anchors for container ids that are not in any unit's source.
    before: tuple[str, ...] = ()
    container: _ContainerRef | None = None


@dataclass
class _DocumentContext:
    units: dict[tuple[Path, int], _UnitContext]
    containers: dict[tuple[Path, int], _Container]
    trailing: dict[Path, tuple[str, ...]]


def _local(element) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _nearest(element, names: frozenset[str]):
    if _local(element) in names:
        return element
    for ancestor in element.iterancestors():
        if _local(ancestor) in names:
            return ancestor
    return None


def _attributes(element, names: tuple[str, ...]) -> str:
    return "".join(
        f' {name}="{html.escape(element.get(name), quote=True)}"'
        for name in names
        if element.get(name) is not None
    )


def _table_layout(table):
    """Row ordinals and which rows are headers, computed once per table."""
    rows = [element for element in table.iter() if _local(element) == "tr"]
    row_index = {id(row): index for index, row in enumerate(rows)}
    header = {
        id(row)
        for section in table
        if _local(section) == "thead"
        for row in section.iter()
        if _local(row) == "tr"
    }
    return row_index, header


def _container_slot(
    element, file_path: Path, index_of: dict[int, int], layouts: dict[int, object]
) -> tuple[object, tuple[Path, int], _ContainerRef] | None:
    """The split container an element sits in, the element that forms it, and
    where in it the element sits.

    An element that is itself an item, row or cell uses that item's listing or
    table; an element inside one uses the same. No early return for a list or
    table element: a whole nested list is still inside the outer item that
    must hold it.
    """
    item = _nearest(element, _ITEM_TAGS)
    if item is not None:
        listing = item.getparent()
        if listing is not None and _local(listing) in _LIST_TAGS:
            key = (file_path, index_of[id(listing)])
            item_index = layouts.get(id(listing))
            if item_index is None:
                item_index = {id(child): index for index, child in enumerate(listing)}
                layouts[id(listing)] = item_index
            ref = _ContainerRef(
                key,
                row=item_index[id(item)],
                item_tag=_local(item),
                item_attrs=_attributes(item, ("value", "type")),
            )
            return listing, key, ref

    cell = _nearest(element, _CELL_TAGS)
    if cell is None:
        return None
    row = cell.getparent()
    if row is None or _local(row) != "tr":
        return None
    table = _nearest(row, frozenset({"table"}))
    if table is None:
        return None
    found = layouts.get(id(table))
    if found is None:
        found = _table_layout(table)
        layouts[id(table)] = found
    row_index, header = found
    key = (file_path, index_of[id(table)])
    ref = _ContainerRef(
        key,
        row=row_index[id(row)],
        col=row.index(cell),
        header=id(row) in header,
        cell_tag=_local(cell),
        cell_attrs=_attributes(cell, ("colspan", "rowspan")),
    )
    return table, key, ref


def _unit_container(
    element, file_path: Path, index_of: dict[int, int], layouts: dict[int, object]
) -> tuple[_Container, _ContainerRef] | None:
    """The split list or table a unit belongs to, and its place in it.

    A whole nested list or table is a unit in its own right but belongs inside
    the item or cell of the container around it; grouping it with that item's
    other units keeps it there without wrapping it in a second <li> or <td>.
    """
    slot = _container_slot(element, file_path, index_of, layouts)
    if slot is None:
        return None
    container_element, key, ref = slot
    enclosing = _container_slot(container_element, file_path, index_of, layouts)
    parent = enclosing[2] if enclosing is not None else None
    if _local(container_element) in _LIST_TAGS:
        container = _Container(
            key,
            "list",
            f"<{_local(container_element)}"
            f"{_attributes(container_element, ('start', 'reversed', 'type'))}>",
            f"</{_local(container_element)}>",
            parent,
        )
    else:
        container = _Container(key, "table", "<table>", "</table>", parent)
    return container, ref


def _document_context(
    reader: EpubReader, by_file: dict[Path, list[Segment]]
) -> _DocumentContext:
    """Per-unit document facts the segment itself does not store.

    A segment holds only the inside of its unit. Its own id, the ids of the
    containers around it, and (for a split list or table) the item, row and
    cell it belongs to live in the document, and each is needed to make the
    exported Markdown keep its anchors and its structure.

    Documents are parsed one at a time and only these facts are kept.
    """
    wanted = set(by_file)
    units: dict[tuple[Path, int], _UnitContext] = {}
    containers: dict[tuple[Path, int], _Container] = {}
    trailing: dict[Path, tuple[str, ...]] = {}
    for document in reader.iter_documents():
        if document.path not in wanted or document.tree is None:
            continue
        path = document.path
        # Segment first: locate_units inserts wrapper spans around loose text,
        # so an index built before it has no position for those units.
        located = locate_units(document.tree, path)
        elements = [element for element in document.tree.iter() if isinstance(element.tag, str)]
        index_of = {id(element): index for index, element in enumerate(elements)}
        unit_ids = {id(element) for element, _mode, _source in located.values()}
        positions = sorted(
            (index_of[id(element)], order)
            for order, (element, _mode, _source) in located.items()
        )
        layouts: dict[int, object] = {}
        before: dict[int, list[str]] = {order: [] for order in located}

        # Ids on containers are not in any unit's source, so no anchor is
        # emitted for them; links that target them land nowhere. Place each
        # before the first unit that follows it in document order.
        for element in elements:
            value = element.get("id")
            if not value or id(element) in unit_ids:
                continue
            if any(id(ancestor) in unit_ids for ancestor in element.iterancestors()):
                continue
            anchor = f'<a id="{html.escape(value, quote=True)}"></a>'
            position = index_of[id(element)]
            following = next((order for pos, order in positions if pos > position), None)
            if following is None:
                trailing[path] = (*trailing.get(path, ()), anchor)
            else:
                before[following].append(anchor)

        for order, (element, _mode, _source) in located.items():
            found = _unit_container(element, path, index_of, layouts)
            container = None
            if found is not None:
                container, ref = found
                containers.setdefault(container.key, container)
                container = ref
            units[(path, order)] = _UnitContext(
                anchor=element.get("id"),
                before=tuple(before[order]),
                container=container,
            )
    return _DocumentContext(units=units, containers=containers, trailing=trailing)


#: Tags that cannot wrap a unit's content; every other block is restored.
_VOID_TAGS = frozenset({"img", "br", "hr"})


def _wrap_block(content: str, element_type: str) -> str:
    """Put a unit's inner markup back inside the block it came from.

    A segment stores the inside of its block. html2text saw a bare <li> and
    made a bullet, and a bare <thead> without <table> and lost the header
    separator; the block element says which list and table it belongs to.
    """
    if not element_type or element_type in _VOID_TAGS:
        return content
    return f"<{element_type}>{content}</{element_type}>"


#: Characters CommonMark reads as syntax wherever they appear, and which text
#: must therefore carry as escaped text. A backslash is not here: html2text
#: already escapes it, and escaping its escapes made "1\." visible as "1\\.".
_MARKDOWN_SPECIAL = re.compile(r"([`*_\[\]<>&|~])")
#: A special character needs no new backslash when an odd run of them already
#: precedes it: that run escapes it in CommonMark. html2text doubles a literal
#: backslash only before the characters its own escaper knows, so the plain
#: text "\&copy;" arrived as one backslash plus "&"; escaping the ampersand
#: again made "\\&copy;", which renders as "\©" instead of the text "&copy;".
_MARKDOWN_ESCAPED_SPECIAL = re.compile(r"(\\*)([`*_\[\]<>&|~])")
_MARKDOWN_LINE_START = re.compile(r"^(\s*)([#+-])(?=\s|$)")
_MARKDOWN_ORDERED_START = re.compile(r"^(\s*)(\d+)([.)])(?=\s|$)")


def _escape_special(match: re.Match) -> str:
    run, char = match.groups()
    return run + ("" if len(run) % 2 else "\\") + char


def _escape_plain_markdown(text: str) -> str:
    """Plain text escaped for CommonMark, after the converter has run.

    Escaping the input for *HTML* was not enough: the converter emitted the
    literal "Use <foo> &copy;", which CommonMark reads as raw HTML and an
    entity, so the tag vanished and the entity rendered as its character.
    A line that begins like a list item is escaped too, or the exported
    paragraph becomes a list. The converter has already escaped backslashes
    and a line's leading number, sign or dash once; they are left alone here.
    """
    escaped = _MARKDOWN_ESCAPED_SPECIAL.sub(_escape_special, text)
    escaped = _MARKDOWN_LINE_START.sub(r"\1\\\2", escaped)
    escaped = _MARKDOWN_ORDERED_START.sub(r"\1\2\\\3", escaped).strip()
    # A backslash at the end of a line is a hard line break. An odd trailing
    # run is made even, so each pair renders as one literal backslash and the
    # line ends normally.
    if (len(escaped) - len(escaped.rstrip("\\"))) % 2:
        escaped += "\\"
    return escaped


class _MarkdownHTML2Text(html2text.HTML2Text):
    """html2text that keeps literal markup literal in the Markdown.

    A unit's source writes a literal "<" as "&lt;" and a literal "&" as
    "&amp;". html2text hands those back as the characters themselves, and
    CommonMark then reads "<foo>" as an HTML tag and "&copy;" as an entity: the
    tag vanished and the entity rendered as its character. Escaping them here,
    where a character entity is known to be text, leaves the Markdown html2text
    generates untouched; escaping its whole output escaped that syntax too.
    """

    def __init__(self, *, literal_markup: bool = True) -> None:
        super().__init__()
        #: Plain text is escaped once, by the caller, after conversion.
        self.literal_markup = literal_markup

    def handle_entityref(self, char: str) -> None:
        self._literal(self.entityref(char))

    def handle_charref(self, char: str) -> None:
        self._literal(self.charref(char))

    def _literal(self, text: str) -> None:
        # In a code span a backslash is a backslash, so leave code alone.
        if text and self.literal_markup and not self.code and not self.pre:
            text = _MARKDOWN_SPECIAL.sub(r"\\\1", text)
        self.handle_data(text, True)


def _unit_content(segment: Segment, wrapper_tag: str) -> str:
    """A unit's markup, ready to sit inside its list item or table cell.

    A paragraph inside an item is put back in that paragraph: left bare, the
    paragraphs of one item merged into one, and every extra one became a
    separate item or column. A plain-text unit is escaped for HTML first, or
    the parser read a literal "<foo>" as a tag and "&copy;" as an entity,
    losing both.
    """
    content = segment.source_content or ""
    if segment.extract_mode == ExtractMode.TEXT:
        content = html.escape(content, quote=False)
    if segment.metadata.element_type == wrapper_tag:
        return content
    return _wrap_block(content, segment.metadata.element_type)


def _container_markup(
    container: _Container,
    group: list[Segment],
    units: dict[tuple[Path, int], _UnitContext],
    nested: dict[tuple[int, int], list[tuple[int, str, _ContainerRef]]] | None = None,
) -> str:
    """Rebuild a split list or table from the units it became.

    ``nested`` holds containers that sit inside this one's items or cells,
    with the document position of their first unit and the item or cell that
    must hold them, so a nested list stays in place instead of following the
    outer list.
    """
    nested = nested or {}
    parts = [container.open_tag]
    if container.kind == "list":
        # Several units can be one item: a <li> holding paragraphs was walked
        # into its children. They are joined inside their item, not made one
        # item each.
        items: dict[int, list[Segment]] = {}
        item_refs: dict[int, _ContainerRef] = {}
        for segment in group:
            ref = units[(segment.file_path, segment.metadata.order_in_file)].container
            item_refs.setdefault(ref.row, ref)
            items.setdefault(ref.row, []).append(segment)
        rows = sorted(set(items) | {row for row, col in nested if col == 0})
        for row in rows:
            children = nested.get((row, 0), ())
            ref = item_refs.get(row) or children[0][2]
            entries = [
                (segment.metadata.order_in_file, _unit_content(segment, ref.item_tag))
                for segment in items.get(row, ())
            ]
            entries.extend((order, text) for order, text, _ref in children)
            parts.append(
                f"<{ref.item_tag}{ref.item_attrs}>"
                + "\n".join(text for _order, text in sorted(entries, key=lambda e: e[0]))
                + f"</{ref.item_tag}>"
            )
    else:
        rows: dict[int, dict[int, list[Segment]]] = {}
        row_refs: dict[int, _ContainerRef] = {}
        cell_refs: dict[tuple[int, int], _ContainerRef] = {}
        for segment in group:
            ref = units[(segment.file_path, segment.metadata.order_in_file)].container
            row_refs.setdefault(ref.row, ref)
            cell_refs.setdefault((ref.row, ref.col), ref)
            rows.setdefault(ref.row, {}).setdefault(ref.col, []).append(segment)
        extras: dict[tuple[int, int], list[tuple[int, str]]] = {}
        for (row, col), children in nested.items():
            row_refs.setdefault(row, children[0][2])
            cell_refs.setdefault((row, col), children[0][2])
            extras.setdefault((row, col), []).extend(
                (order, text) for order, text, _ref in children
            )

        def cell_entries(row: int, col: int) -> list[tuple[int, str]]:
            ref = cell_refs[(row, col)]
            entries = [
                (segment.metadata.order_in_file, _unit_content(segment, ref.cell_tag))
                for segment in rows.get(row, {}).get(col, ())
            ]
            entries.extend(extras.get((row, col), ()))
            return sorted(entries, key=lambda e: e[0])

        def row_markup(row: int) -> str:
            cols = sorted(set(rows.get(row, {})) | {col for r, col in extras if r == row})
            cells = "".join(
                f"<{cell_refs[(row, col)].cell_tag}{cell_refs[(row, col)].cell_attrs}>"
                + "\n".join(text for _order, text in cell_entries(row, col))
                + f"</{cell_refs[(row, col)].cell_tag}>"
                for col in cols
            )
            return f"<tr>{cells}</tr>"

        all_rows = sorted(set(rows) | set(row_refs))
        header = [row for row in all_rows if row_refs[row].header]
        body = [row for row in all_rows if not row_refs[row].header]
        if header:
            parts.append("<thead>" + "".join(row_markup(row) for row in header) + "</thead>")
        if body:
            parts.append("<tbody>" + "".join(row_markup(row) for row in body) + "</tbody>")
    parts.append(container.close_tag)
    return "".join(parts)


_LIST_ITEM = re.compile(r"^(\s*)([*+-]|\d+[.)])\s+(.*)$")
#: Emitted indentation per nesting level.
_NESTED_INDENT = 4


def _keep_list_items_whole(markdown: str) -> str:
    """Indent an item's later blocks so they stay inside the item, and keep a
    nested list nested.

    html2text starts every item after the first two spaces in, starts a second
    paragraph of an item at column zero, and gives a nested list an indent
    derived from the width of the lists around it. The first two are fixed
    here. The third is read back as a nesting level: every level of the source
    list adds a fixed indent, so a nested list stays inside its item instead
    of being flattened into a sibling of the outer one.
    """
    lines = markdown.splitlines()
    matches = [_LIST_ITEM.match(line) for line in lines]
    if not any(matches):
        return markdown
    # Open levels: (marker kind, the indent html2text used, content column).
    levels: list[tuple[str, int, int]] = []
    fixed: list[str] = []
    for line, match in zip(lines, matches, strict=True):
        if match:
            _indent, marker, rest = match.groups()
            indent = len(_indent)
            kind = "ol" if marker[0].isdigit() else "ul"
            if not levels:
                # The first item is at column zero; every later sibling of the
                # outermost list is written at two, which is this level's base.
                levels = [(kind, 2, 0)]
                depth = 0
            else:
                while len(levels) > 1 and indent < levels[-1][1]:
                    levels.pop()
                parent_kind, parent_base, _parent_col = levels[-1]
                # A nested list's indent is the width of its parent list:
                # three for an ordered parent, two for an unordered one.
                child_base = parent_base + (3 if parent_kind == "ol" else 2)
                if indent >= child_base:
                    depth = len(levels)
                    levels.append((kind, indent, 0))
                else:
                    depth = len(levels) - 1
            content_col = depth * _NESTED_INDENT + len(marker) + 1
            levels[depth] = (kind, levels[depth][1], content_col)
            del levels[depth + 1 :]
            nested = _LIST_ITEM.match(rest)
            if nested is None:
                fixed.append(" " * (depth * _NESTED_INDENT) + f"{marker} {rest}")
            else:
                # The item's first block is a nested list, and html2text put
                # its first item on the parent's marker line. On one line,
                # CommonMark reads a sibling of that first item as a child of
                # it. Put the parent marker on its own line and start the
                # nested list one level in, where every item stays a sibling.
                nested_marker, nested_rest = nested.group(2), nested.group(3)
                nested_kind = "ol" if nested_marker[0].isdigit() else "ul"
                child_depth = depth + 1
                child_base = levels[depth][1] + (3 if kind == "ol" else 2)
                child_col = child_depth * _NESTED_INDENT + len(nested_marker) + 1
                levels.append((nested_kind, child_base, child_col))
                fixed.append(" " * (depth * _NESTED_INDENT) + marker)
                fixed.append(
                    " " * (child_depth * _NESTED_INDENT)
                    + f"{nested_marker} {nested_rest}"
                )
        elif line.strip() and levels and not line.startswith(" " * levels[-1][2]):
            fixed.append(" " * levels[-1][2] + line)
        else:
            fixed.append(line)
    return "\n".join(fixed)


def _render_block_body(
    block: ChapterBlock,
    by_file: dict[Path, list[Segment]],
    img_map: dict[str, str],
    document_destinations: dict[str, str] | None = None,
    context: _DocumentContext | None = None,
) -> list[str]:
    """Render one chapter block's segments to markdown lines (no heading)."""
    units = (context.units if context else {}) or {}
    containers = (context.containers if context else {}) or {}
    trailing = (context.trailing if context else {}) or {}
    # A container inside another container's item or cell is rebuilt as part
    # of that item, not emitted as a separate block after the outer container.
    nested_of: dict[tuple[Path, int], list[tuple[Path, int]]] = {}
    for key, container in containers.items():
        parent = container.parent
        if parent is not None and parent.key in containers:
            nested_of.setdefault(parent.key, []).append(key)
    nested_keys = {key for keys in nested_of.values() for key in keys}
    lines: list[str] = []
    for file_path in block.files:
        segments = by_file.get(file_path, [])
        emitted: set[tuple[Path, int]] = set()
        contexts = {
            segment.metadata.order_in_file: units.get(
                (file_path, segment.metadata.order_in_file), _UnitContext()
            )
            for segment in segments
        }
        groups: dict[tuple[Path, int], list[Segment]] = {}
        for segment in segments:
            ref = contexts[segment.metadata.order_in_file].container
            if ref is not None:
                groups.setdefault(ref.key, []).append(segment)

        def emit_anchors(ctx: _UnitContext) -> None:
            lines.extend(ctx.before)
            if ctx.anchor:
                lines.append(f'<a id="{html.escape(ctx.anchor, quote=True)}"></a>')

        def container_segments(
            key: tuple[Path, int], groups=groups
        ) -> list[Segment]:
            found = list(groups.get(key, ()))
            for child_key in nested_of.get(key, ()):
                found.extend(container_segments(child_key))
            return sorted(found, key=lambda s: s.metadata.order_in_file)

        def render_container(
            key: tuple[Path, int], groups=groups
        ) -> str:
            children: dict[tuple[int, int], list[tuple[int, str, _ContainerRef]]] = {}
            for child_key in nested_of.get(key, ()):
                child_group = groups.get(child_key, ())
                child_ref = containers[child_key].parent
                if not child_group or child_ref is None:
                    continue
                order = min(s.metadata.order_in_file for s in child_group)
                children.setdefault((child_ref.row, child_ref.col), []).append(
                    (order, render_container(child_key), child_ref)
                )
            return _container_markup(containers[key], groups.get(key, []), units, children)

        for segment in segments:
            content = segment.source_content or ""
            if not content.strip():
                continue
            ctx = contexts[segment.metadata.order_in_file]
            ref = ctx.container
            if ref is not None and ref.key in containers:
                # A nested container is rendered by its outer item's markup.
                if ref.key in nested_keys or ref.key in emitted:
                    continue
                emitted.add(ref.key)
                # Every item, row or cell of the container is a unit; rebuild
                # it once, from all of them, rather than emitting each one as
                # a bullet or a loose cell.
                for item in container_segments(ref.key):
                    emit_anchors(contexts[item.metadata.order_in_file])
                container = containers[ref.key]
                text = _html_to_markdown(
                    render_container(ref.key),
                    file_path,
                    img_map,
                    document_destinations,
                )
                if container.kind == "list":
                    text = _keep_list_items_whole(text)
                if text.strip():
                    lines.append(text)
                    lines.append("")
                continue

            if segment.extract_mode == ExtractMode.TEXT:
                # Plain text, escaped for HTML so a literal tag stays text.
                # The converter is told to leave the characters alone; the
                # Markdown escape then runs once. Run after the converter's
                # own escapes it doubled them, putting a visible backslash in
                # "1." prose.
                text = _escape_plain_markdown(
                    _html_to_markdown(
                        html.escape(content, quote=False),
                        file_path,
                        img_map,
                        document_destinations,
                        True,  # plain text: escape its characters below
                    )
                )
            else:
                text = _html_to_markdown(
                    _wrap_block(content, segment.metadata.element_type),
                    file_path,
                    img_map,
                    document_destinations,
                )
            if text.strip():
                emit_anchors(ctx)
                lines.append(text)
                lines.append("")
        for anchor in trailing.get(file_path, ()):
            lines.append(anchor)
    return lines


def _chapter_file_name(index: int, block: ChapterBlock) -> str:
    return f"{index:03d}_{_sanitize_filename(block.title)}.md"


def _document_destinations(blocks: list[ChapterBlock]) -> dict[str, str]:
    """EPUB document path -> the Markdown file that will contain it."""
    destinations: dict[str, str] = {}
    for index, block in enumerate(blocks, start=1):
        name = _chapter_file_name(index, block)
        for file_path in block.files:
            destinations.setdefault(file_path.as_posix(), name)
    return destinations


def _combined_destinations(blocks: list[ChapterBlock]) -> dict[str, str]:
    """EPUB document path -> the anchor of its chapter in the combined file."""
    return {
        file_path.as_posix(): f"#tepub-chapter-{index}"
        for index, block in enumerate(blocks, start=1)
        for file_path in block.files
    }


def _write_chapter_files(
    blocks: list[ChapterBlock], bodies: list[list[str]], output_dir: Path
) -> list[Path]:
    """One numbered file per chapter block."""
    output_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []
    for idx, (block, body) in enumerate(zip(blocks, bodies, strict=True), start=1):
        md_path = output_dir / _chapter_file_name(idx, block)
        md_path.write_text("\n".join([f"# {block.title}", "", *body]), encoding="utf-8")
        created_files.append(md_path)
    return created_files


def _write_combined_file(
    input_epub: Path,
    blocks: list[ChapterBlock],
    bodies: list[list[str]],
    output_dir: Path,
    chapter_files: list[Path] | None = None,
) -> Path:
    """Every block in one file named after the EPUB, a ## heading per block."""
    output_dir.mkdir(parents=True, exist_ok=True)
    all_lines = [f"# {input_epub.stem}", "", "---", ""]
    for idx, (block, body) in enumerate(zip(blocks, bodies, strict=True), start=1):
        # A link from another document needs somewhere to land.
        all_lines += [f'<a id="tepub-chapter-{idx}"></a>', f"## {block.title}", "", *body]
        # A separator between chapters, not after the last.
        if idx < len(blocks):
            all_lines += ["---", ""]
    combined_path = output_dir / f"{input_epub.stem}.md"
    # A book named 001_introduction.epub wrote the combined edition over the
    # first chapter file, 001_introduction.md. The names it must not take are
    # the chapter files of this export even when only the combined one is
    # written, and the comparison is case-insensitive because the usual
    # filesystems are: "001_Introduction.md" and "001_introduction.md" are one
    # file there.
    if chapter_files is None:
        taken = {
            _chapter_file_name(index, block).casefold()
            for index, block in enumerate(blocks, start=1)
        }
    else:
        taken = {path.name.casefold() for path in chapter_files}
    counter = 0
    while combined_path.name.casefold() in taken:
        counter += 1
        suffix = "-combined" if counter == 1 else f"-combined-{counter}"
        combined_path = output_dir / f"{input_epub.stem}{suffix}.md"
    combined_path.write_text("\n".join(all_lines), encoding="utf-8")
    return combined_path


def _rendered_chapters(
    settings: AppSettings, input_epub: Path, image_mapping: dict[str, str] | None
) -> tuple[list[ChapterBlock], list[list[str]]]:
    """Blocks and bodies rendered with links to the chapter files they become."""
    by_file, blocks, img_map, context = _prepare_export(settings, input_epub, image_mapping)
    destinations = _document_destinations(blocks)
    return blocks, [
        _render_block_body(block, by_file, img_map, destinations, context)
        for block in blocks
    ]


def export_markdown(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
    image_mapping: dict[str, str] | None = None,
) -> tuple[list[Path], Path]:
    """Both exports, the chapter files and the combined file.

    The segments are loaded, the book parsed and every unit converted once for
    the two; called one after the other, each did all of it again.
    """
    blocks, bodies = _rendered_chapters(settings, input_epub, image_mapping)
    chapter_files = _write_chapter_files(blocks, bodies, output_dir)
    return (
        chapter_files,
        _write_combined_file(input_epub, blocks, bodies, output_dir, chapter_files),
    )


def export_to_markdown(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
    image_mapping: dict[str, str] | None = None,
) -> list[Path]:
    """
    Export extracted segments to numbered markdown files.

    Uses smart chapter grouping: multiple spine files between TOC entries
    are combined into a single markdown file per chapter.

    Args:
        settings: Application settings
        input_epub: Path to source EPUB
        output_dir: Directory where markdown files will be written
        image_mapping: Optional mapping from EPUB image paths to extracted filenames

    Returns:
        List of created markdown file paths
    """
    blocks, bodies = _rendered_chapters(settings, input_epub, image_mapping)
    return _write_chapter_files(blocks, bodies, output_dir)


def export_combined_markdown(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
    image_mapping: dict[str, str] | None = None,
) -> Path:
    """
    Export all segments to a single combined markdown file.

    Uses smart chapter grouping: multiple spine files between TOC entries
    are combined under a single ## heading per chapter.

    Args:
        settings: Application settings
        input_epub: Path to source EPUB
        output_dir: Directory where markdown file will be written
        image_mapping: Optional mapping from EPUB image paths to extracted filenames

    Returns:
        Path to the created combined markdown file
    """
    by_file, blocks, img_map, context = _prepare_export(settings, input_epub, image_mapping)
    destinations = _combined_destinations(blocks)
    bodies = [
        _render_block_body(block, by_file, img_map, destinations, context)
        for block in blocks
    ]
    return _write_combined_file(input_epub, blocks, bodies, output_dir)
