"""Element operations for injecting translations into XHTML trees."""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from epub_io.xhtml import parse_fragment
from state.models import ExtractMode, Segment


def prepare_original(element: etree._Element) -> None:
    element.set("data-lang", "original")


def _clear_children(element: etree._Element) -> None:
    for child in list(element):
        element.remove(child)


def _set_text_only(element: etree._Element, text: str) -> None:
    _clear_children(element)
    element.text = text


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


def _strip_ids(element: etree._Element) -> None:
    """Translated copies sit beside their originals; repeating ids would make
    every in-book link ambiguous, and epubcheck rejects duplicates (D5)."""
    for node in element.iter():
        if isinstance(node.tag, str):
            node.attrib.pop("id", None)


def build_translation_element(
    original: etree._Element, segment: Segment, translation: str
) -> etree._Element:
    clone = deepcopy(original)
    clone.tail = None
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(clone, translation)
    else:
        _set_html_content(clone, translation)
    _strip_ids(clone)
    clone.set("data-lang", "translation")
    return clone


def insert_translation_after(
    original: etree._Element, translation_element: etree._Element
) -> None:
    parent = original.getparent()
    if parent is None:
        raise ValueError("Original element missing parent; cannot insert translation")
    parent.insert(parent.index(original) + 1, translation_element)
