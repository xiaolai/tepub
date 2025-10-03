from __future__ import annotations

from lxml import html

from state.models import ExtractMode, Segment


def _clone_element(element: html.HtmlElement) -> html.HtmlElement:
    return html.fragment_fromstring(html.tostring(element, encoding="unicode"))


def prepare_original(element: html.HtmlElement) -> None:
    element.attrib["data-lang"] = "original"


def _set_text_only(element: html.HtmlElement, text: str) -> None:
    element.text = text
    for child in list(element):
        element.remove(child)


def _set_html_content(element: html.HtmlElement, markup: str) -> None:
    """Set element's content from HTML markup string.

    Clears element and populates with parsed HTML fragments.
    Properly handles the fragment_fromstring wrapper to avoid tag leaks.
    """
    element.clear()
    if not markup:
        return

    # Parse HTML - fragment_fromstring with create_parent=True returns container
    container = html.fragment_fromstring(f"<wrapper>{markup}</wrapper>", create_parent=True)

    # Container has <wrapper> as first child - extract its contents
    if len(container) > 0 and getattr(container[0], "tag", None) == "wrapper":
        wrapper = container[0]
        element.text = wrapper.text
        for child in wrapper:
            element.append(child)
    else:
        # Fallback: shouldn't happen but handle gracefully
        element.text = container.text
        for child in container:
            element.append(child)


def build_translation_element(
    original: html.HtmlElement, segment: Segment, translation: str
) -> html.HtmlElement:
    clone = _clone_element(original)
    clone.attrib["data-lang"] = "translation"
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(clone, translation)
    else:
        _set_html_content(clone, translation)
    return clone


def insert_translation_after(
    original: html.HtmlElement, translation_element: html.HtmlElement
) -> None:
    parent = original.getparent()
    if parent is None:
        raise ValueError("Original element missing parent; cannot insert translation")
    index = parent.index(original)
    parent.insert(index + 1, translation_element)
