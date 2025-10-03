"""Tests for footnote filtering during audiobook generation."""

from pathlib import Path
from unittest.mock import Mock

from lxml import html as lxml_html

from audiobook.preprocess import _reextract_filtered, segment_to_text
from state.models import ExtractMode, Segment, SegmentMetadata


def test_reextract_filtered_removes_sup_footnotes():
    """Test that <a><sup> footnote references are removed."""
    html_content = """
    <p>This is a sentence with a footnote<a href="#fn1"><sup>1</sup></a> reference.</p>
    """

    # Mock segment
    segment = Segment(
        segment_id="test-001",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content=html_content,
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    # Mock reader
    mock_doc = Mock()
    element = lxml_html.fromstring(html_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    # Execute
    result = _reextract_filtered(segment, mock_reader)

    # Verify footnote reference removed
    assert result == "This is a sentence with a footnote reference."
    assert "<sup>" not in result
    assert "1" not in result


def test_reextract_filtered_removes_sub_footnotes():
    """Test that <a><sub> footnote references are removed."""
    html_content = """
    <p>This is text<a href="#note2"><sub>2</sub></a> with subscript note.</p>
    """

    segment = Segment(
        segment_id="test-002",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content=html_content,
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    mock_doc = Mock()
    element = lxml_html.fromstring(html_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    result = _reextract_filtered(segment, mock_reader)

    assert result == "This is text with subscript note."
    assert "<sub>" not in result
    assert "2" not in result


def test_reextract_filtered_preserves_regular_links():
    """Test that regular links (without sup/sub) are preserved."""
    html_content = """
    <p>Visit <a href="https://example.com">our website</a> for more.</p>
    """

    segment = Segment(
        segment_id="test-003",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content=html_content,
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    mock_doc = Mock()
    element = lxml_html.fromstring(html_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    result = _reextract_filtered(segment, mock_reader)

    # Link text should be preserved
    assert "our website" in result


def test_reextract_filtered_multiple_footnotes():
    """Test removing multiple footnote references."""
    html_content = """
    <p>First<a><sup>1</sup></a> and second<a><sup>2</sup></a> footnotes.</p>
    """

    segment = Segment(
        segment_id="test-004",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content=html_content,
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    mock_doc = Mock()
    element = lxml_html.fromstring(html_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    result = _reextract_filtered(segment, mock_reader)

    assert result == "First and second footnotes."
    assert "1" not in result
    assert "2" not in result


def test_segment_to_text_uses_reader_when_provided():
    """Test that segment_to_text uses reader for re-extraction when provided."""
    segment = Segment(
        segment_id="test-005",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content="<p>Text with<a><sup>1</sup></a> footnote.</p>",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    # Mock reader setup
    mock_doc = Mock()
    element = lxml_html.fromstring(segment.source_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    # With reader - should filter
    result_with_reader = segment_to_text(segment, reader=mock_reader)
    assert result_with_reader == "Text with footnote."

    # Without reader - should keep HTML as-is (footnote number appears)
    result_without_reader = segment_to_text(segment, reader=None)
    assert "1" in result_without_reader


def test_segment_to_text_fallback_on_reextraction_failure():
    """Test fallback to stored content if re-extraction fails."""
    segment = Segment(
        segment_id="test-006",
        file_path=Path("chapter.xhtml"),
        xpath="//p",
        extract_mode=ExtractMode.HTML,
        source_content="<p>Fallback text content</p>",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=0,
            order_in_file=0
        )
    )

    # Mock reader that raises error
    mock_reader = Mock()
    mock_reader.read_document_by_path.side_effect = Exception("EPUB read failed")

    # Should fallback to stored content
    result = segment_to_text(segment, reader=mock_reader)
    assert result == "Fallback text content"


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
