from lxml import etree

from epub_io.xhtml import XHTML_NS
from injection.html_ops import _set_html_content


def _element(tag: str):
    return etree.Element(f"{{{XHTML_NS}}}{tag}", nsmap={None: XHTML_NS})


def test_set_html_content_removes_wrapper():
    """The parsing wrapper never reaches the output."""
    element = _element("ul")
    _set_html_content(element, "<li>Item 1</li><li>Item 2</li>")

    result = etree.tostring(element, encoding="unicode")
    assert "wrapper" not in result
    assert [child.text for child in element] == ["Item 1", "Item 2"]


def test_set_html_content_with_table():
    element = _element("table")
    _set_html_content(
        element, "<thead><tr><th>Header</th></tr></thead><tbody><tr><td>Data</td></tr></tbody>"
    )
    assert [etree.QName(child).localname for child in element] == ["thead", "tbody"]


def test_set_html_content_keeps_markup_in_the_xhtml_namespace():
    """Parsed as HTML and appended, markup used to serialise with xmlns=""."""
    element = _element("p")
    _set_html_content(element, "Line<br>break &nbsp; and <em>more</em>")
    assert b'xmlns=""' not in etree.tostring(element)
    assert all(etree.QName(child).namespace == XHTML_NS for child in element)


def test_set_html_content_empty_markup():
    element = _element("div")
    element.text = "Old content"
    _set_html_content(element, "")

    assert element.text is None
    assert len(element) == 0


def test_leading_text_on_a_list_is_wrapped_in_an_item():
    element = _element("ol")
    _set_html_content(element, "Prefix<li>Item</li>")

    assert element.text is None
    assert element[0].tag == f"{{{XHTML_NS}}}li"
    assert element[0].text == "Prefix"
    assert element[1].tag == f"{{{XHTML_NS}}}li"


def test_translated_markup_cannot_introduce_execution():
    element = _element("div")
    _set_html_content(
        element,
        '<img src="x.png" onerror="alert(1)"/>'
        '<a href="java&#9;script:alert(1)">x</a>'
        "<script>alert(2)</script>",
    )

    markup = etree.tostring(element, encoding="unicode")
    assert "onerror" not in markup
    assert "javascript:" not in markup.replace("java\tscript:", "")
    assert "<script" not in markup
