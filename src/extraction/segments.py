"""Split a content document into translation units.

One rule decides what a unit is (decision D1 in the improvement plan):

- a table, list or figure is one unit, whole;
- a block element with no block descendants is a unit;
- a block element with text of its own beside block children ("mixed
  content", such as <blockquote>Before<p>In</p>After</blockquote>) is one unit,
  whole, so no text is left behind;
- otherwise a block is a container and its children are examined.

SVG, MathML, epub:switch alternatives, scripts, styles and code listings are
never translated. The old rules extracted a paragraph inside a blockquote twice,
and both levels of a nested blockquote with text, so text was translated and
injected twice.

Segmentation is a pure function of the document, so injection finds a unit
again by running it on the same document and checking the unit's source text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

from lxml import etree

from epub_io.xhtml import XHTML_NS, local_name, parse_fragment, text_of
from extraction.cleaners import normalize_punctuation
from state.models import ExtractMode, Segment, SegmentMetadata

ATOMIC_TAGS = frozenset({"ul", "ol", "dl", "table", "figure"})

BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "caption", "center", "dd", "details",
        "div", "dl", "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)

# Never translated, and never looked inside.
SKIPPED_TAGS = frozenset({"svg", "math", "switch", "script", "style", "pre", "template"})

# Presentational wrappers dropped from the HTML sent for translation; their
# text stays. A link is kept when it has a target or an anchor (footnote
# references do); converted books also leave <a class="..."/> elements that
# carry nothing once styling goes, and those are unwrapped like spans.
_UNWRAPPED_INLINE = ("span", "font", "a")

OPS_TYPE = "{http://www.idpf.org/2007/ops}type"

# Attributes the translation must carry back unchanged: link targets, anchors,
# images and note semantics. Classes and styles are noise to a model.
_KEPT_ATTRIBUTES = frozenset({"href", "src", "id", "alt", "title", "role", OPS_TYPE})


def _is_block(element: etree._Element) -> bool:
    return local_name(element) in BLOCK_TAGS


def _has_block_descendant(element: etree._Element) -> bool:
    return any(_is_block(d) for d in element.iterdescendants() if isinstance(d.tag, str))


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _has_own_content(element: etree._Element) -> bool:
    """Text that belongs to ``element`` itself rather than to a block child."""
    if _has_text(element.text):
        return True
    for child in element:
        if _has_text(child.tail):
            return True
        if (
            isinstance(child.tag, str)
            and not _is_block(child)
            and local_name(child) not in SKIPPED_TAGS
            and _has_text(text_of(child))
        ):
            return True
    return False


def iter_units(container: etree._Element) -> Iterator[tuple[etree._Element, ExtractMode]]:
    """Yield (element, mode) for every unit below ``container``, in document order."""
    for child in container:
        if not isinstance(child.tag, str):
            continue
        name = local_name(child)
        if name in SKIPPED_TAGS:
            continue
        if name in ATOMIC_TAGS:
            yield child, ExtractMode.HTML
        elif _has_block_descendant(child):
            # A container, whatever its tag: converted books often wrap lists and
            # paragraphs in a <span>, which is invalid HTML but common.
            if _has_own_content(child):
                yield child, ExtractMode.HTML
            else:
                yield from iter_units(child)
        elif _is_block(child):
            mode = ExtractMode.HTML if _has_inline_markup(child) else ExtractMode.TEXT
            yield child, mode
        # Inline content directly in a container that is walked is that
        # container's own text; a container with any is a unit itself above.


def _has_inline_markup(element: etree._Element) -> bool:
    """True when a leaf block holds markup a translation must keep.

    Sent as plain text, such a paragraph lost its links, footnote references,
    emphasis and images in translated-only output.
    """
    return any(
        isinstance(node.tag, str)
        and (local_name(node) not in _UNWRAPPED_INLINE or _is_anchor(node))
        for node in element.iterdescendants()
    )


_ANCHOR_ATTRIBUTES = ("id", OPS_TYPE, "role", "href")


def _is_anchor(node: etree._Element) -> bool:
    """A span, font or link that points somewhere, or that something points at
    or reads: a link, an endnote target, a print page-break marker."""
    return any(node.get(name) is not None for name in _ANCHOR_ATTRIBUTES)


def _unwrap(node: etree._Element) -> None:
    """Replace ``node`` by its content, keeping text and tail in place."""
    parent = node.getparent()
    if parent is None:
        return
    index = parent.index(node)
    children = list(node)
    previous = node.getprevious()
    lead = node.text or ""
    if previous is not None:
        previous.tail = (previous.tail or "") + lead
    else:
        parent.text = (parent.text or "") + lead
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    last = children[-1] if children else node.getprevious()
    tail = node.tail or ""
    if last is not None and last is not node:
        last.tail = (last.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(node)


def _translatable_text(element: etree._Element) -> str:
    """Text outside SVG, MathML and other skipped content."""
    parts = [element.text or ""]
    for child in element:
        if isinstance(child.tag, str) and local_name(child) not in SKIPPED_TAGS:
            parts.append(_translatable_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def body_of(root: etree._Element) -> etree._Element | None:
    return root.find(f"{{{XHTML_NS}}}body")


def _extract_text(element: etree._Element) -> str:
    return normalize_punctuation(" ".join(text_of(element).split()))


def _plain_copy(element: etree._Element) -> etree._Element:
    """A copy with XHTML names un-namespaced and only meaningful attributes kept.

    SVG and MathML are left exactly as written: cleaning them as HTML turned an
    SVG <image xlink:href> into an invalid <image src> and dropped its size.
    """
    clone = deepcopy(element)
    for node in clone.iter():
        if not isinstance(node.tag, str):
            continue
        qname = etree.QName(node)
        if qname.namespace != XHTML_NS:
            continue
        node.tag = qname.localname
        kept = {name: value for name, value in node.attrib.items() if name in _KEPT_ATTRIBUTES}
        node.attrib.clear()
        for name, value in kept.items():
            node.set(name, value)
    # Presentational wrappers go; ones with a target or an anchor stay.
    for node in list(clone.iter()):
        if (
            node is not clone
            and isinstance(node.tag, str)
            and local_name(node) in _UNWRAPPED_INLINE
            and not _is_anchor(node)
        ):
            _unwrap(node)
    etree.cleanup_namespaces(clone)
    return clone


_EPUB_DECLARATION = ' xmlns:epub="http://www.idpf.org/2007/ops"'


def _extract_inner_html(element: etree._Element) -> str:
    clone = _plain_copy(element)
    parts = [clone.text or ""]
    for child in clone:
        parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
    # Each serialised child repeats the epub: declaration it needs; the parser
    # that reads translations back declares it once instead.
    return "".join(parts).replace(_EPUB_DECLARATION, "").strip()


def clean_markup(markup: str) -> str:
    """Markup cleaned by the rule that builds a unit's source: presentational
    wrappers and links with no target or anchor are unwrapped."""
    text, elements = parse_fragment(markup)
    holder = etree.Element(f"{{{XHTML_NS}}}div")
    holder.text = text
    holder.extend(elements)
    return _extract_inner_html(holder)


def unit_id(file_path: Path, order: int) -> str:
    """Unique per book: the full EPUB path is hashed, so equal file names in
    different folders no longer collide."""
    digest = hashlib.sha1(file_path.as_posix().encode("utf-8")).hexdigest()[:8]
    return f"{file_path.stem}-{digest}-{order}"


def source_of(element: etree._Element, mode: ExtractMode) -> str:
    return _extract_text(element) if mode == ExtractMode.TEXT else _extract_inner_html(element)


def _unit_source(element: etree._Element, mode: ExtractMode) -> str | None:
    """The unit's source, or None when it has no words to translate.

    A block holding only a picture, such as a title page's SVG cover, used to
    become a unit and be sent to the model.
    """
    if not _has_text(_translatable_text(element)):
        return None
    return source_of(element, mode) or None


def iter_segments(
    tree: etree._Element,
    file_path: Path,
    spine_index: int,
) -> Iterator[Segment]:
    body = body_of(tree)
    if body is None:
        return
    root_tree = tree.getroottree()
    order = 0
    for element, mode in iter_units(body):
        content = _unit_source(element, mode)
        if content is None:
            continue
        order += 1
        yield Segment(
            segment_id=unit_id(file_path, order),
            file_path=file_path,
            xpath=root_tree.getpath(element),  # for diagnosis only; never used to locate
            extract_mode=mode,
            source_content=content,
            metadata=SegmentMetadata(
                element_type=local_name(element), spine_index=spine_index, order_in_file=order
            ),
        )


def locate_units(
    tree: etree._Element, file_path: Path
) -> dict[int, tuple[etree._Element, ExtractMode, str]]:
    """Map order_in_file to (element, mode, source text) for a parsed document.

    Uses exactly the enumeration iter_segments used, so a stored segment's order
    finds its element; callers compare the source text before trusting it.
    """
    body = body_of(tree)
    if body is None:
        return {}
    found: dict[int, tuple[etree._Element, ExtractMode, str]] = {}
    order = 0
    for element, mode in iter_units(body):
        content = _unit_source(element, mode)
        if content is None:
            continue
        order += 1
        found[order] = (element, mode, content)
    return found


def loose_body_text(tree: etree._Element) -> int:
    """Count runs of text that sit directly in <body>, outside any block.

    There is no element to translate them in place, so they stay untranslated;
    extraction reports them rather than dropping them silently. Rare: in a
    19-book corpus it was mostly a dictionary's index of links.
    """
    body = body_of(tree)
    if body is None:
        return 0
    runs = 1 if _has_text(body.text) else 0
    for child in body:
        if _has_text(child.tail):
            runs += 1
        if (
            isinstance(child.tag, str)
            and local_name(child) not in SKIPPED_TAGS
            and local_name(child) not in ATOMIC_TAGS
            and not _is_block(child)
            and not _has_block_descendant(child)
            and _has_text(text_of(child))
        ):
            runs += 1
    return runs
