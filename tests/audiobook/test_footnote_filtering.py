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


def _spoken(tmp_path: Path, body: str, notes: str = "") -> str:
    reader, segments = _book(tmp_path, body, notes)
    return " ".join(filter(None, (segment_to_text(s, reader=reader) for s in segments)))


def test_list_items_and_line_breaks_do_not_merge_words(tmp_path: Path) -> None:
    """itertext() joined structural boundaries with nothing: "FirstSecond"."""
    body = "<ul><li>First point</li><li>Second point</li></ul><p>Line one<br/>line two.</p>"

    spoken = _spoken(tmp_path, body)

    assert "First point Second point" in spoken
    assert "Line one line two." in spoken


def test_script_and_style_contents_are_not_narrated(tmp_path: Path) -> None:
    body = "<p>Before<script>var x = 1;</script> after.</p>"

    assert _spoken(tmp_path, body) == "Before after."


def test_a_note_definition_inside_a_whole_list_is_not_narrated(tmp_path: Path) -> None:
    """Checking only the unit and its ancestors missed a definition in an item."""
    body = '<ol><li id="fn1">A note.</li><li>Visible item.</li></ol>'

    spoken = _spoken(tmp_path, body)

    assert "A note." not in spoken
    assert "Visible item." in spoken


def test_list_punctuation_follows_the_whole_item() -> None:
    """The full stop was put into li.text, before the item's inline children."""
    segment = Segment(
        segment_id="list",
        file_path=Path("c.xhtml"),
        xpath="/html/body/ul",
        extract_mode=ExtractMode.HTML,
        source_content="<li>Red <em>blue</em> green</li>",
        metadata=SegmentMetadata(element_type="ul", spine_index=0, order_in_file=1),
    )

    assert segment_to_text(segment, reader=None) == "Red blue green."


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<p>This is a sentence with a footnote<a href="#fn1"><sup>1</sup></a> reference.</p>'
            '<p id="fn1">A note.</p>',
            "This is a sentence with a footnote reference.",
        ),
        (
            '<p>This is text<a href="#note2"><sub>2</sub></a> with subscript note.</p>'
            '<p id="note2">A note.</p>',
            "This is text with subscript note.",
        ),
        (
            '<p>First<a href="#fn1"><sup>1</sup></a> and second<a href="#fn2"><sup>2</sup></a>'
            ' and third<a href="#fn3"><sup>3</sup></a> notes.</p>'
            '<p id="fn1">A.</p><p id="fn2">B.</p><p id="fn3">C.</p>',
            "First and second and third notes.",
        ),
    ],
    ids=["sup", "sub", "several"],
)
def test_note_references_are_not_spoken(tmp_path: Path, body: str, expected: str) -> None:
    assert _spoken(tmp_path, body) == expected


def test_a_note_in_another_document_is_verified(tmp_path: Path) -> None:
    reader, segments = _book(
        tmp_path,
        '<p>Text<a href="notes.xhtml#note1"><sup>1</sup></a> here.</p>',
        '<p id="note1">A note.</p>',
    )

    spoken = " ".join(filter(None, (segment_to_text(s, reader=reader) for s in segments)))

    assert spoken == "Text here."


def test_an_unverified_fragment_link_keeps_its_superscript(tmp_path: Path) -> None:
    """A fragment is not proof of a note: this one points at nothing."""
    assert _spoken(tmp_path, '<p>See x<a href="#power"><sup>2</sup></a>.</p>') == "See x2."


def test_an_exponent_linked_to_external_documentation_is_spoken(tmp_path: Path) -> None:
    body = '<p>See x<a href="https://docs.example/power#exponent"><sup>2</sup></a>.</p>'

    assert _spoken(tmp_path, body) == "See x2."


@pytest.mark.parametrize(
    "link",
    [
        pytest.param('<a epub:type="noteref" href="#fn1">1</a>', id="plain-text"),
        pytest.param('<sup><a epub:type="noteref" href="#fn1">1</a></sup>', id="inside-sup"),
    ],
)
def test_explicit_noteref_is_removed_whatever_its_children(tmp_path: Path, link: str) -> None:
    body = f'<p>Claim{link}.</p><p id="fn1">A note.</p>'

    assert _spoken(tmp_path, body) == "Claim."


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
            ('<a href="#fn1"><sup>1</sup></a>', False, "unverified in-page fragment"),
            ("<a><sup>1</sup></a>", True, "bare marker, no href"),
            ('<a href="notes.xhtml#n1"><sup>1</sup></a>', False, "unverified cross-file"),
            ('<a class="footnote" href="x"><sup>1</sup></a>', True, "class hint"),
            ('<a epub:type="noteref" href="#fn1">1</a>', True, "explicit, plain text"),
            ('<a href="https://example.com"><sup>2</sup></a>', False, "external link"),
            ('<a href="formula.xhtml"><sup>2</sup></a>', False, "linked formula"),
        ],
    )
    def test_noteref_classification(self, markup, expected, label):
        from lxml import html as lxml_html

        from audiobook.preprocess import _is_noteref

        link = lxml_html.fromstring(f"<p>{markup}</p>").xpath(".//a")[0]

        assert _is_noteref(link) is expected, label
