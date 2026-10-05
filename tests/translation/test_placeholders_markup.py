"""Inline markup travels as numbered markers, and comes back as the original tags.

Asked to keep HTML, TranslateGemma moved or dropped anchors and note links in
about 30% of the paragraphs of a link-dense book. Short markers are easier for a
model to keep, and the tags it never sees cannot be mangled.
"""

from __future__ import annotations

from translation.markup import markup_mismatch, protect, restore

SOURCE = (
    'A claim<a epub:type="noteref" id="r1" href="notes.xhtml#n1"><sup>1</sup></a> '
    'with <em>emphasis</em> &amp; an anchor<a id="filepos42"/> and <img src="i.png" alt="pic"/>.'
)


def test_protect_replaces_tags_with_markers_and_unescapes_text() -> None:
    text, _tags = protect(SOURCE)
    assert text == "A claim⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧ with ⟦3⟧emphasis⟦/3⟧ & an anchor⟦4⟧ and ⟦5⟧."


def test_restore_rebuilds_the_original_tags_around_translated_words() -> None:
    _text, tags = protect(SOURCE)
    reply = "一个论断⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧，带有⟦3⟧强调⟦/3⟧和锚点⟦4⟧，还有⟦5⟧。"
    rebuilt = restore(reply, tags)
    assert 'href="notes.xhtml#n1"' in rebuilt and 'id="filepos42"' in rebuilt
    assert markup_mismatch(SOURCE, rebuilt) is None
    assert "<em>强调</em>" in rebuilt


def test_text_is_escaped_when_rebuilt() -> None:
    _text, tags = protect("R&amp;D <b>now</b>")
    assert restore("R&D ⟦1⟧现在⟦/1⟧ <script>", tags) == "R&amp;D <b>现在</b> &lt;script&gt;"


def test_markers_written_loosely_are_still_understood() -> None:
    _text, tags = protect('See<a href="#n"><sup>2</sup></a>.')
    assert restore("见⟦ 1 ⟧⟦2⟧2⟦ /2 ⟧⟦/1⟧。", tags) == '见<a href="#n"><sup>2</sup></a>。'


def test_a_broken_pair_is_dropped_and_then_caught_by_the_check() -> None:
    source = 'Note<a href="#n"><sup>3</sup></a>.'
    _text, tags = protect(source)
    rebuilt = restore("注释⟦1⟧⟦2⟧3⟦/2⟧。", tags)  # the closing ⟦/1⟧ was lost
    assert "<a" not in rebuilt and "<sup>3</sup>" in rebuilt
    assert "a" in (markup_mismatch(source, rebuilt) or "")


def test_unknown_and_repeated_markers_are_ignored() -> None:
    _text, tags = protect('x<a id="k"/>y')
    assert restore("甲⟦1⟧乙⟦1⟧丙⟦9⟧", tags) == '甲<a id="k"/>乙丙'


def test_a_closing_marker_before_its_opening_one_is_dropped() -> None:
    _text, tags = protect('see <a href="#n">here</a>')
    assert restore("⟦/1⟧见⟦1⟧这里⟦/1⟧", tags) == '见<a href="#n">这里</a>'


def test_crossed_pairs_keep_only_the_one_closed_properly() -> None:
    _text, tags = protect('<a href="#x">a <b>b</b></a>')
    assert restore("⟦1⟧甲⟦2⟧乙⟦/1⟧丙⟦/2⟧", tags) == '<a href="#x">甲乙</a>丙'


def test_block_structure_and_marker_lookalikes_keep_the_html_route() -> None:
    assert protect("<li>one</li><li>two</li>") is None
    assert protect("a ⟦1⟧ in the source") is None
    assert protect('x<svg xmlns="http://www.w3.org/2000/svg"/>') is None


def test_a_line_break_travels_as_a_newline() -> None:
    """TranslateGemma dropped a lone marker standing for <br/> on every title
    page line it saw; it keeps newlines."""
    text, markers = protect("Ching Kwan Lee, author of<br/>The Specter of\n   Global China")
    assert text == "Ching Kwan Lee, author of\nThe Specter of Global China"
    rebuilt = restore("李静君，\n《全球中国的幽灵》作者\n", markers)
    assert rebuilt == "李静君，<br/>《全球中国的幽灵》作者"


def test_newlines_the_model_added_are_joined_without_a_space_in_chinese() -> None:
    _text, markers = protect('One <a href="#n">long</a> paragraph.')
    assert restore("一个⟦1⟧很长的⟦/1⟧\n段落。\nEnd\nhere", markers) == (
        '一个<a href="#n">很长的</a>段落。End here'
    )
