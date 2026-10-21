"""Element operations for injecting translations into XHTML trees."""

from __future__ import annotations

import re
from copy import deepcopy

from lxml import etree

from epub_io.xhtml import XHTML_NS, local_name, parse_fragment
from state.models import ExtractMode, Segment

XML_NS = "http://www.w3.org/XML/1998/namespace"

#: Elements that execute or navigate the reader. A translated fragment has no
#: business introducing any of them.
_UNSAFE_ELEMENTS = frozenset(
    {
        "script", "iframe", "object", "embed", "applet", "base", "meta", "link",
        "template", "frame", "frameset", "portal",
    }
)
_URL_ATTRIBUTES = frozenset({"href", "src", "xlink:href", "poster", "action", "formaction"})
_UNSAFE_SCHEMES = ("javascript:", "vbscript:", "data:text/html", "data:application/xhtml")


def _safe_url(value: str) -> bool:
    normalised = re.sub(r"[\x00-\x20]", "", value).lower()
    return not normalised.startswith(_UNSAFE_SCHEMES)


def _attribute_name(name: str) -> str:
    if name.startswith("{"):
        return etree.QName(name).localname.lower()
    return name.lower()


def _sanitize_fragment(elements: list[etree._Element]) -> list[etree._Element]:
    """Remove executable elements and attributes from a translated fragment.

    markup_mismatch rejects a reply that adds them and asks for another, but a
    reply can still arrive that the check cannot see (a tag it exempts, an
    unfamiliar attribute spelling). This is the guard that runs regardless.
    """
    for element in elements:
        for node in list(element.iter()):
            if not isinstance(node.tag, str):
                continue
            if local_name(node) in _UNSAFE_ELEMENTS:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                continue
            for name in list(node.attrib):
                local = _attribute_name(name)
                if local.startswith("on"):
                    del node.attrib[name]
                elif local in _URL_ATTRIBUTES and not _safe_url(node.attrib[name]):
                    del node.attrib[name]
                elif local == "srcdoc":
                    del node.attrib[name]
    return [element for element in elements if element.getparent() is not None]


def _set_language(element: etree._Element, language: str | None) -> None:
    """Declare the language of an element's own text.

    Both attributes are required: XHTML 1.1 (EPUB 2) defines lang and xml:lang,
    and readers and screen readers pick whichever they implement.
    """
    if not language:
        return
    element.set("lang", language)
    element.set(f"{{{XML_NS}}}lang", language)


def _mark(
    element: etree._Element,
    side: str,
    *,
    data_attributes: bool,
    language: str | None = None,
) -> None:
    """Mark an element as the original or the translation.

    A class works in every EPUB version. data-lang is added only where data-*
    attributes are valid: EPUB 2 content is XHTML 1.1, where epubcheck rejects
    them. lang/xml:lang are valid everywhere and let a bilingual book declare
    which paragraphs are which.
    """
    token = f"tepub-{side}"
    classes = (element.get("class") or "").split()
    if token not in classes:
        element.set("class", " ".join([*classes, token]))
    if data_attributes:
        element.set("data-lang", side)
    _set_language(element, language)


def prepare_original(
    element: etree._Element,
    *,
    data_attributes: bool = True,
    language: str | None = None,
) -> None:
    _mark(element, "original", data_attributes=data_attributes, language=language)


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


#: Containers that cannot hold text directly, and the chain that can. A
#: translator sometimes prefixes a block of markup with a word or a note; on
#: an <ol> that text made the exported document invalid (epubcheck RSC-005).
_TEXT_WRAPPERS: dict[str, tuple[str, ...]] = {
    "ul": ("li",),
    "ol": ("li",),
    "dl": ("dd",),
    "tr": ("td",),
    "thead": ("tr", "td"),
    "tbody": ("tr", "td"),
    "tfoot": ("tr", "td"),
    "table": ("tbody", "tr", "td"),
    "select": ("option",),
    "optgroup": ("option",),
}


def _attach_leading_text(element: etree._Element, text: str) -> None:
    """Put text where the element is allowed to hold it."""
    if not text:
        return
    path = _TEXT_WRAPPERS.get(local_name(element))
    if path is None or not text.strip():
        element.text = text
        return
    holder = element
    for name in path:
        child = etree.Element(f"{{{XHTML_NS}}}{name}")
        if local_name(holder) == "table":
            # tbody belongs after thead and before tfoot, not at position 0.
            tfoot = next(
                (node for node in holder if local_name(node) == "tfoot"), None
            )
            if tfoot is not None:
                holder.insert(holder.index(tfoot), child)
            else:
                holder.append(child)
        else:
            holder.insert(0, child)
        holder = child
    holder.text = text


def _set_text_only(element: etree._Element, text: str) -> None:
    _clear_children(element)
    element.text = None
    _attach_leading_text(element, text)
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
    children = _sanitize_fragment(children)
    element.text = None
    _attach_leading_text(element, text or "")
    for child in children:
        element.append(child)
    _blocks_where_required(element)


_OPS_TYPE = "{http://www.idpf.org/2007/ops}type"


def _is_page_break(node: etree._Element) -> bool:
    return node.get("role") == "doc-pagebreak" or "pagebreak" in (node.get(_OPS_TYPE) or "").split()


def _unmark_page_break(node: etree._Element) -> None:
    """Keep a page-break marker's text without marking the same page again.

    Only empty markers are dropped outright; one holding text stays in the
    copy, where its role or epub:type announced the printed page a second time.
    """
    if node.get("role") == "doc-pagebreak":
        node.attrib.pop("role", None)
    types = (node.get(_OPS_TYPE) or "").split()
    if "pagebreak" in types:
        types.remove("pagebreak")
        if types:
            node.set(_OPS_TYPE, " ".join(types))
        else:
            node.attrib.pop(_OPS_TYPE, None)


def _strip_ids(element: etree._Element) -> None:
    """Translated copies sit beside their originals; repeating ids would make
    every in-book link ambiguous, and epubcheck rejects duplicates (D5).

    Page-break markers go too: they mark where a page of the printed book
    begins, and a copy marked the same page twice for page lists and screen
    readers, as role="doc-pagebreak" with its id gone.

    xml:id is an ID in its own right; leaving it on a copy is a duplicate XML
    ID, and a parser rejects the document.
    """
    for node in list(element.iter()):
        if not isinstance(node.tag, str):
            continue
        if node is not element and _is_page_break(node):
            if not len(node) and not (node.text or "").strip():
                _remove_keeping_tail(node)
                continue
            _unmark_page_break(node)
        node.attrib.pop("id", None)
        node.attrib.pop(f"{{{XML_NS}}}id", None)


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
    language: str | None = None,
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
    _mark(clone, "translation", data_attributes=data_attributes, language=language)
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
    source_language: str | None = None,
    target_language: str | None = None,
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
    _mark(
        original,
        "original",
        data_attributes=data_attributes,
        language=source_language,
    )
    item.append(original)

    translated = etree.Element(f"{{{XHTML_NS}}}{tag}")
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(translated, translation)
    else:
        _set_html_content(translated, translation)
    _strip_ids(translated)
    _mark(
        translated,
        "translation",
        data_attributes=data_attributes,
        language=target_language,
    )
    if tag == "span":
        item.append(etree.Element(f"{{{XHTML_NS}}}br"))
    item.append(translated)
