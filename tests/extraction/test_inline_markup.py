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


SVG_COVER = (
    '<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
    'viewBox="0 0 600 800"><image width="600" height="800" xlink:href="cover.jpg"/></svg></div>'
)


def test_a_picture_with_no_words_is_not_a_unit() -> None:
    """A title page's SVG cover used to become a unit: sent to the model, and
    rewritten into invalid SVG (xlink:href turned into src, sizes dropped)."""
    assert _units(SVG_COVER) == []


def test_svg_and_mathml_inside_a_unit_are_left_exactly_as_written() -> None:
    (unit,) = _units(
        '<p>The value <math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">'
        '<mi mathvariant="bold">x</mi></math> and a figure '
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" width="10"><rect width="1" height="1"/></svg>.</p>'
    )
    source = unit.source_content
    assert 'mathvariant="bold"' in source and 'display="inline"' in source
    assert 'viewBox="0 0 1 1"' in source and 'width="10"' in source


def test_anchor_and_page_break_spans_are_kept() -> None:
    """Seen in a real book: endnote targets and print page markers are empty
    spans. Treated as presentational, the heading went as plain text and
    translated-only output lost both, breaking the notes' links."""
    (unit,) = _units(
        '<h1><span epub:type="pagebreak" id="page_1" role="doc-pagebreak" title="1"/>'
        '<span id="EndnotePhraseInText0"/>One</h1>'
    )
    assert unit.extract_mode == ExtractMode.HTML
    assert 'id="page_1"' in unit.source_content and 'epub:type="pagebreak"' in unit.source_content
    assert 'id="EndnotePhraseInText0"' in unit.source_content


def test_unwrapping_keeps_text_in_order() -> None:
    (unit,) = _units("<p>a<span>b<em>c</em>d</span>e<span>f</span></p>")
    assert unit.source_content == "ab<em>c</em>def"
