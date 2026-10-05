"""Element operations for injecting translations into XHTML trees."""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from epub_io.xhtml import parse_fragment
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
