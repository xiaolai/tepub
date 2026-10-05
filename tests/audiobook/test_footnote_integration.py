"""Footnote patterns from real books, narrated from a real EPUB."""

from pathlib import Path

from tests.audiobook.test_footnote_filtering import _spoken


def test_dynasty_epub_footnote_patterns(tmp_path: Path) -> None:
    """Patterns from the Dynasty EPUB: asterisk markers, numbered cross-file notes,
    and a subscript fragment link, which the filter treats as a note."""
    body = (
        '<p id="p1">Mars, the Spiller of Blood, had planted his seed in a mortal womb.'
        '<a href="#ftn3" id="ftn3a"><sup>*1</sup></a></p>'
        '<p id="p3">The Roman character.<a href="notes.xhtml#n1" id="n1a"><sup>1</sup></a>'
        ' Even more text.<a href="notes.xhtml#n2" id="n2a"><sup>2</sup></a> Final sentence.</p>'
        '<p id="p10">Chemical formula H<a href="#fn1"><sub>2</sub></a>O here.</p>'
    )
    spoken = _spoken(tmp_path, body)
    assert "Mars, the Spiller of Blood, had planted his seed in a mortal womb." in spoken
    assert "The Roman character. Even more text. Final sentence." in spoken
    assert "Chemical formula HO here." in spoken
    assert "*1" not in spoken
