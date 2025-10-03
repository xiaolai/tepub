from lxml import html

from injection.html_ops import _set_html_content


def test_set_html_content_removes_wrapper():
    """Test that wrapper parsing artifact doesn't leak into output."""
    element = html.fromstring("<ul></ul>")
    markup = "<li>Item 1</li><li>Item 2</li>"
    _set_html_content(element, markup)

    result = html.tostring(element, encoding="unicode")

    assert "<wrapper>" not in result
    assert "</wrapper>" not in result
    assert "<li>Item 1</li>" in result
    assert "<li>Item 2</li>" in result
    assert len(element) == 2


def test_set_html_content_with_table():
    """Test wrapper removal with table HTML."""
    element = html.fromstring("<table></table>")
    markup = "<thead><tr><th>Header</th></tr></thead><tbody><tr><td>Data</td></tr></tbody>"
    _set_html_content(element, markup)

    result = html.tostring(element, encoding="unicode")
    assert "<wrapper>" not in result
    assert "<thead>" in result
    assert "<tbody>" in result


def test_set_html_content_empty_markup():
    """Test empty markup clears element."""
    element = html.fromstring("<div>Old content</div>")
    _set_html_content(element, "")

    assert element.text is None
    assert len(element) == 0
