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
) -> str:
    """
    Convert HTML content to markdown, preserving formatting and images.

    Args:
        html_content: HTML content to convert
        document_path: Path of the document containing this content (for resolving image refs)
        image_mapping: Mapping from EPUB image paths to extracted filenames
        document_destinations: Mapping from EPUB document paths to the Markdown
            file that contains them, for rewriting local links

    Returns:
        Markdown formatted text with image references
    """
    try:
        # Configure html2text
        h = html2text.HTML2Text()
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
    dict[tuple[Path, int], str],
]:
    """Load segments, group them by file, and build chapter blocks.

    Shared by both exporters, which previously carried identical copies of this
    setup and could drift apart. Also returns each unit's own id: the segment
    stores a block's inside, so an id on the block itself (a note definition,
    a section target) is not in its source and would otherwise have no anchor.
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
    return by_file, blocks, img_map, _unit_ids(reader, by_file)


def _unit_ids(
    reader: EpubReader, by_file: dict[Path, list[Segment]]
) -> dict[tuple[Path, int], str]:
    """(document, order_in_file) -> the unit element's own id, when it has one.

    Documents are parsed one at a time and only their ids are kept; reading
    every document through the reader's cache would hold the whole book in
    memory for the whole export.
    """
    wanted = set(by_file)
    ids: dict[tuple[Path, int], str] = {}
    for document in reader.iter_documents():
        if document.path not in wanted or document.tree is None:
            continue
        for order, (element, _mode, _source) in locate_units(
            document.tree, document.path
        ).items():
            value = element.get("id")
            if value:
                ids[(document.path, order)] = value
    return ids


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


def _render_block_body(
    block: ChapterBlock,
    by_file: dict[Path, list[Segment]],
    img_map: dict[str, str],
    document_destinations: dict[str, str] | None = None,
    unit_ids: dict[tuple[Path, int], str] | None = None,
) -> list[str]:
    """Render one chapter block's segments to markdown lines (no heading)."""
    lines: list[str] = []
    for file_path in block.files:
        for segment in by_file.get(file_path, []):
            content = segment.source_content or ""
            if not content.strip():
                continue

            if segment.extract_mode == ExtractMode.TEXT:
                # Plain text parsed as HTML lost literal tags ("Use <foo>" came
                # out as "Use") and interpreted literal entities ("&copy;").
                content = html.escape(content, quote=False)
            else:
                content = _wrap_block(content, segment.metadata.element_type)

            # Convert HTML to markdown, preserving images
            text = _html_to_markdown(content, file_path, img_map, document_destinations)
            if text.strip():
                unit_id = (unit_ids or {}).get((file_path, segment.metadata.order_in_file))
                if unit_id:
                    lines.append(
                        f'<a id="{html.escape(unit_id, quote=True)}"></a>'
                    )
                lines.append(text)
                lines.append("")
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
    # first chapter file, 001_introduction.md, written a moment earlier.
    taken = set(chapter_files or ())
    if combined_path in taken:
        combined_path = output_dir / f"{input_epub.stem}-combined.md"
    counter = 1
    while combined_path in taken:
        counter += 1
        combined_path = output_dir / f"{input_epub.stem}-combined-{counter}.md"
    combined_path.write_text("\n".join(all_lines), encoding="utf-8")
    return combined_path


def _rendered_chapters(
    settings: AppSettings, input_epub: Path, image_mapping: dict[str, str] | None
) -> tuple[list[ChapterBlock], list[list[str]]]:
    """Blocks and bodies rendered with links to the chapter files they become."""
    by_file, blocks, img_map, unit_ids = _prepare_export(settings, input_epub, image_mapping)
    destinations = _document_destinations(blocks)
    return blocks, [
        _render_block_body(block, by_file, img_map, destinations, unit_ids)
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
    by_file, blocks, img_map, unit_ids = _prepare_export(settings, input_epub, image_mapping)
    destinations = _combined_destinations(blocks)
    bodies = [
        _render_block_body(block, by_file, img_map, destinations, unit_ids)
        for block in blocks
    ]
    return _write_combined_file(input_epub, blocks, bodies, output_dir)
