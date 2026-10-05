"""Units with inline markup are sent as HTML, with the attributes links need.

Paragraphs were sent as plain text, so a translation could only replace their
text: in translated-only output every footnote reference, link, emphasis and
inline image disappeared, and the note bodies they pointed to were orphaned.
"""

from __future__ import annotations

from pathlib import Path

from epub_io.xhtml import parse_xhtml
from extraction.segments import iter_segments
from state.models import ExtractMode


def _units(body: str):
    markup = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>t</title></head><body>{body}</body></html>"
    )
    return list(iter_segments(parse_xhtml(markup.encode()).root, Path("c.xhtml"), 0))


def test_a_plain_paragraph_stays_text() -> None:
    (unit,) = _units("<p>Just words.</p>")
    assert unit.extract_mode == ExtractMode.TEXT
    assert unit.source_content == "Just words."


def test_a_paragraph_with_inline_markup_is_html() -> None:
    (unit,) = _units(
        '<p class="body" style="x">A claim<a epub:type="noteref" id="r1" href="notes.xhtml#n1">'
        '<sup>1</sup></a> with <em>emphasis</em> and <img src="i.png" alt="pic"/>.</p>'
    )
    assert unit.extract_mode == ExtractMode.HTML
    source = unit.source_content
    assert 'href="notes.xhtml#n1"' in source and 'id="r1"' in source
    assert 'epub:type="noteref"' in source
    assert "<em>emphasis</em>" in source and 'src="i.png"' in source and 'alt="pic"' in source
    assert "class=" not in source and "style=" not in source


def test_spans_are_unwrapped_but_links_are_kept() -> None:
    (unit,) = _units('<p><span class="x">Styled</span> and <a href="#t">linked</a>.</p>')
    assert unit.source_content == 'Styled and <a href="#t">linked</a>.'
