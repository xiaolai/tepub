"""Tests for footnote filtering during audiobook generation.

They run against real EPUBs built for the purpose. Their predecessors mocked
reader.read_document_by_path and doc.tree.xpath, which is how a reader method
that did not exist went unnoticed while the suite stayed green.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from audiobook.preprocess import segment_to_text
from config import AppSettings
from epub_io.reader import EpubReader
from extraction.segments import iter_segments
from state.models import ExtractMode, Segment, SegmentMetadata
from tests.epub_builder import build_epub


def _book(tmp_path: Path, body: str, notes: str = ""):
    """A reader and the extracted segments of a one-chapter book."""
    chapters = [("ch1.xhtml", "Chapter", body)]
    if notes:
        chapters.append(("notes.xhtml", "Notes", notes))
    epub = build_epub(tmp_path / "book.epub", chapters)
    reader = EpubReader(epub, AppSettings(work_dir=tmp_path / "work"))
    segments = [
        segment
        for document in reader.iter_documents()
        for segment in iter_segments(document.tree, document.path, document.spine_item.index)
    ]
    return reader, segments


def _spoken(tmp_path: Path, body: str) -> str:
    reader, segments = _book(tmp_path, body)
    return " ".join(filter(None, (segment_to_text(s, reader=reader) for s in segments)))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<p>This is a sentence with a footnote<a href="#fn1"><sup>1</sup></a> reference.</p>',
            "This is a sentence with a footnote reference.",
        ),
        (
            '<p>This is text<a href="#note2"><sub>2</sub></a> with subscript note.</p>',
            "This is text with subscript note.",
        ),
        (
            '<p>First<a href="#a"><sup>1</sup></a> and second<a href="#b"><sup>2</sup></a>'
            ' and third<a href="#c"><sup>3</sup></a> notes.</p>',
            "First and second and third notes.",
        ),
    ],
    ids=["sup", "sub", "several"],
)
def test_note_references_are_not_spoken(tmp_path: Path, body: str, expected: str) -> None:
    assert _spoken(tmp_path, body) == expected


def test_regular_links_are_spoken(tmp_path: Path) -> None:
    body = '<p>Visit <a href="https://example.com">our website</a> for more information.</p>'
    assert _spoken(tmp_path, body) == "Visit our website for more information."


def test_a_unit_that_no_longer_matches_falls_back_to_its_stored_text(tmp_path: Path) -> None:
    reader, (segment,) = _book(tmp_path, '<p>Current<a href="#n"><sup>1</sup></a> text.</p>')
    stale = segment.model_copy(update={"source_content": "Text from an older edition."})
    assert segment_to_text(stale, reader=reader) == "Text from an older edition."


def test_segment_to_text_does_not_swallow_a_failed_document_lookup(tmp_path: Path) -> None:
    """A document missing from the EPUB is an error, not a reason to guess."""
    reader, (segment,) = _book(tmp_path, "<p>Text.</p>")
    elsewhere = segment.model_copy(update={"file_path": Path("missing.xhtml")})
    with pytest.raises(KeyError):
        segment_to_text(elsewhere, reader=reader)


def test_segment_to_text_skips_table_and_figure():
    """Test that table and figure elements are skipped regardless of reader."""
    table_segment = Segment(
        segment_id="test-007",
        file_path=Path("chapter.xhtml"),
        xpath="//table",
        extract_mode=ExtractMode.HTML,
        source_content="<table><tr><td>Data</td></tr></table>",
        metadata=SegmentMetadata(
            element_type="table",
            spine_index=0,
            order_in_file=0
        )
    )

    figure_segment = Segment(
        segment_id="test-008",
        file_path=Path("chapter.xhtml"),
        xpath="//figure",
        extract_mode=ExtractMode.HTML,
        source_content="<figure><img src='pic.jpg'/></figure>",
        metadata=SegmentMetadata(
            element_type="figure",
            spine_index=0,
            order_in_file=0
        )
    )

    mock_reader = Mock()

    assert segment_to_text(table_segment, reader=mock_reader) is None
    assert segment_to_text(figure_segment, reader=mock_reader) is None


class TestNoterefDetection:
    """A superscript link is only a note reference when it actually looks like one."""

    import pytest

    @pytest.mark.parametrize(
        "markup,expected,label",
        [
            ('<a href="#fn1"><sup>1</sup></a>', True, "in-page fragment"),
            ("<a><sup>1</sup></a>", True, "bare marker, no href"),
            ('<a href="notes.xhtml#n1"><sup>1</sup></a>', True, "cross-file note"),
            ('<a class="footnote" href="x"><sup>1</sup></a>', True, "class hint"),
            ('<a href="https://example.com"><sup>2</sup></a>', False, "external link"),
            ('<a href="formula.xhtml"><sup>2</sup></a>', False, "linked formula"),
        ],
    )
    def test_noteref_classification(self, markup, expected, label):
        from lxml import html as lxml_html

        from audiobook.preprocess import _is_noteref

        link = lxml_html.fromstring(f"<p>{markup}</p>").xpath(".//a")[0]

        assert _is_noteref(link) is expected, label
