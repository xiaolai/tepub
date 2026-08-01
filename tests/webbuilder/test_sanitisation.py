"""The web export renders untrusted book content in a browser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webbuilder.dom import clean_html


@pytest.mark.parametrize("relative_path", [None, Path("Text/ch1.xhtml")])
@pytest.mark.parametrize(
    "markup,forbidden",
    [
        ("<script>alert(1)</script>", "alert(1)"),
        ('<img src=x onerror="alert(1)">', "onerror"),
        ('<p onclick="alert(1)">hi</p>', "onclick"),
        ('<a href="javascript:alert(1)">x</a>', "javascript:"),
        ('<a href="JaVaScRiPt:alert(1)">x</a>', "avascript:"),
        ('<iframe src="//evil"></iframe>', "<iframe"),
        ('<object data="//evil"></object>', "<object"),
        ('<form action="javascript:alert(1)"></form>', "javascript:"),
    ],
)
def test_dangerous_markup_is_removed(markup, forbidden, relative_path):
    """Sanitisation must not depend on relative_path being supplied.

    URL handling used to live only in _rewrite_links, which runs only when a
    relative_path is passed, so javascript: URLs survived the default path.
    """
    out = clean_html(f"<html><body>{markup}</body></html>", relative_path=relative_path)

    assert forbidden not in out


def test_legitimate_content_survives():
    out = clean_html(
        "<html><body><p>Real text.</p>"
        '<a href="https://example.com">link</a>'
        '<img src="pic.png"></body></html>'
    )

    assert "Real text." in out
    assert "example.com" in out
    assert "pic.png" in out


def test_book_data_cannot_break_out_of_script_element():
    """json.dumps does not escape '<', so '</script>' closed the element."""
    from webbuilder.assets import _escape_for_script_element

    payload = json.dumps({"title": "Evil</script><img src=x onerror=alert(1)>"})
    escaped = _escape_for_script_element(payload)

    assert "</script>" not in escaped
    assert "<" not in escaped
    # Still decodes to the original value.
    assert json.loads(escaped)["title"] == json.loads(payload)["title"]
