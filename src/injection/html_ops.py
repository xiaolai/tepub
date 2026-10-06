"""Element operations for injecting translations into XHTML trees."""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from epub_io.xhtml import XHTML_NS, local_name, parse_fragment
from state.models import ExtractMode, Segment


def _mark(element: etree._Element, side: str, *, data_attributes: bool) -> None:
    """Mark an element as the original or the translation.

    A class works in every EPUB version. data-lang is added only where data-*
    attributes are valid: EPUB 2 content is XHTML 1.1, where epubcheck rejects
    them.
    """
    token = f"tepub-{side}"
    classes = (element.get("class") or "").split()
    if token not in classes:
        element.set("class", " ".join([*classes, token]))
    if data_attributes:
        element.set("data-lang", side)


def prepare_original(element: etree._Element, *, data_attributes: bool = True) -> None:
    _mark(element, "original", data_attributes=data_attributes)


def _clear_children(element: etree._Element) -> None:
    for child in list(element):
        element.remove(child)


# Elements whose content must be blocks in XHTML 1.1 (EPUB 2).
_BLOCKS_ONLY = frozenset({"blockquote", "form"})


def _blocks_where_required(element: etree._Element) -> None:
    """Wrap inline content in a <p> where only blocks may stand.

    Converted books put <font> straight inside <blockquote>; filled with a
    translation, the presentational wrappers went and the text stood loose in
    the blockquote, an epubcheck error in EPUB 2. A <p> is valid in every
    version and looks the same.
    """
    from extraction.segments import BLOCK_TAGS

    if local_name(element) not in _BLOCKS_ONLY:
        return
    if any(isinstance(child.tag, str) and local_name(child) in BLOCK_TAGS for child in element):
        return
    if not (element.text or "").strip() and not len(element):
        return
    paragraph = etree.Element(f"{{{XHTML_NS}}}p")
    paragraph.text, element.text = element.text, None
    for child in list(element):
        paragraph.append(child)
    element.append(paragraph)


def _set_text_only(element: etree._Element, text: str) -> None:
    _clear_children(element)
    element.text = text
    _blocks_where_required(element)


def _set_html_content(element: etree._Element, markup: str) -> None:
    """Replace the element's content with parsed markup, keeping its attributes.

    The markup lands in the XHTML namespace; parsed as HTML and appended to an
    XHTML tree it serialised with xmlns="", in no namespace at all.
    """
    _clear_children(element)
    element.text = None
    if not markup:
        return
    text, children = parse_fragment(markup)
    element.text = text or None
    for child in children:
        element.append(child)
    _blocks_where_required(element)


_OPS_TYPE = "{http://www.idpf.org/2007/ops}type"


def _is_page_break(node: etree._Element) -> bool:
    return node.get("role") == "doc-pagebreak" or "pagebreak" in (node.get(_OPS_TYPE) or "").split()


def _strip_ids(element: etree._Element) -> None:
    """Translated copies sit beside their originals; repeating ids would make
    every in-book link ambiguous, and epubcheck rejects duplicates (D5).

    Page-break markers go too: they mark where a page of the printed book
    begins, and a copy marked the same page twice for page lists and screen
    readers, as role="doc-pagebreak" with its id gone.
    """
    for node in list(element.iter()):
        if not isinstance(node.tag, str):
            continue
        if (
            node is not element
            and _is_page_break(node)
            and not len(node)
            and not (node.text or "").strip()
        ):
            _remove_keeping_tail(node)
            continue
        node.attrib.pop("id", None)


def _remove_keeping_tail(node: etree._Element) -> None:
    parent = node.getparent()
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def build_translation_element(
    original: etree._Element,
    segment: Segment,
    translation: str,
    *,
    data_attributes: bool = True,
) -> etree._Element:
    clone = deepcopy(original)
    clone.tail = None
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(clone, translation)
    else:
        _set_html_content(clone, translation)
    _strip_ids(clone)
    clone.attrib.pop("data-lang", None)
    classes = [c for c in (clone.get("class") or "").split() if c != "tepub-original"]
    if classes:
        clone.set("class", " ".join(classes))
    else:
        clone.attrib.pop("class", None)
    _mark(clone, "translation", data_attributes=data_attributes)
    return clone


def insert_translation_after(
    original: etree._Element, translation_element: etree._Element
) -> None:
    parent = original.getparent()
    if parent is None:
        raise ValueError("Original element missing parent; cannot insert translation")
    parent.insert(parent.index(original) + 1, translation_element)


# Elements whose siblings are structure: a translated copy beside a list item
# renumbers the list, beside a cell it adds a column.
ITEM_TAGS = frozenset({"li", "td", "th", "dt", "dd"})


def inject_into_item(
    item: etree._Element,
    segment: Segment,
    translation: str,
    *,
    data_attributes: bool = True,
) -> None:
    """Put a list item's or cell's translation inside it, under the original.

    The original content moves into its own marked wrapper, so the reader's
    hide-original and hide-translation switches still work: marking the item
    itself would hide the translation with it. A <dt> holds only inline
    content in EPUB 2, so its wrappers are spans.
    """
    tag = "span" if local_name(item) == "dt" else "div"
    original = etree.Element(f"{{{XHTML_NS}}}{tag}")
    original.text, item.text = item.text, None
    for child in list(item):
        original.append(child)  # moves it, tail included
    _mark(original, "original", data_attributes=data_attributes)
    item.append(original)

    translated = etree.Element(f"{{{XHTML_NS}}}{tag}")
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(translated, translation)
    else:
        _set_html_content(translated, translation)
    _strip_ids(translated)
    _mark(translated, "translation", data_attributes=data_attributes)
    if tag == "span":
        item.append(etree.Element(f"{{{XHTML_NS}}}br"))
    item.append(translated)
