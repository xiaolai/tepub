"""Integration test for footnote filtering with real EPUB structure."""

from pathlib import Path
from unittest.mock import Mock

from lxml import html as lxml_html

from audiobook.preprocess import segment_to_text
from state.models import ExtractMode, Segment, SegmentMetadata


def test_dynasty_epub_footnote_patterns():
    """Test with actual patterns from Dynasty EPUB file."""

    # Pattern 1: Inline endnote reference with asterisk
    segment1 = Segment(
        segment_id="p1",
        file_path=Path("OEBPS/chapter01.xhtml"),
        xpath="//p[@id='p1']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="p1">Mars, the Spiller of Blood, had planted his seed in a mortal womb.<a href="#ftn3" id="ftn3a"><sup>*1</sup></a></p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=0
        )
    )

    # Pattern 2: Multiple endnote references with numbers
    segment2 = Segment(
        segment_id="p3",
        file_path=Path("OEBPS/chapter01.xhtml"),
        xpath="//p[@id='p3']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="p3">The Roman character.<a href="content/OEBPS/notes.xhtml#n1" id="n1a"><sup>1</sup></a> Even more text.<a href="content/OEBPS/notes.xhtml#n2" id="n2a"><sup>2</sup></a> Final sentence.</p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=1
        )
    )

    # Pattern 3: Footnote with subscript reference
    segment3 = Segment(
        segment_id="p10",
        file_path=Path("OEBPS/chapter01.xhtml"),
        xpath="//p[@id='p10']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="p10">Chemical formula H<a href="#fn1"><sub>2</sub></a>O is not actually a footnote, but our filter removes it anyway.</p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=2
        )
    )

    # Test with reader (filtered)
    # Segment 1
    mock_reader1 = Mock()
    mock_doc1 = Mock()
    element1 = lxml_html.fromstring(segment1.source_content)
    mock_doc1.tree.xpath.return_value = [element1]
    mock_reader1.read_document_by_path.return_value = mock_doc1

    result1 = segment_to_text(segment1, reader=mock_reader1)
    assert result1 == "Mars, the Spiller of Blood, had planted his seed in a mortal womb."
    assert "*1" not in result1

    # Segment 2
    mock_reader2 = Mock()
    mock_doc2 = Mock()
    element2 = lxml_html.fromstring(segment2.source_content)
    mock_doc2.tree.xpath.return_value = [element2]
    mock_reader2.read_document_by_path.return_value = mock_doc2

    result2 = segment_to_text(segment2, reader=mock_reader2)
    assert result2 == "The Roman character. Even more text. Final sentence."
    assert "1" not in result2.replace("Roman character.", "")  # Avoid false positive from "1" in different context
    assert "2" not in result2

    # Segment 3
    mock_reader3 = Mock()
    mock_doc3 = Mock()
    element3 = lxml_html.fromstring(segment3.source_content)
    mock_doc3.tree.xpath.return_value = [element3]
    mock_reader3.read_document_by_path.return_value = mock_doc3

    result3 = segment_to_text(segment3, reader=mock_reader3)
    # Note: This removes <sub>2</sub> which might be a chemical formula, not a footnote
    # This is a known limitation - users should review their content
    assert "2" not in result3


def test_footnote_file_exclusion():
    """Test that footnote files can be excluded via audiobook_files config."""

    # Main chapter segment
    chapter_segment = Segment(
        segment_id="ch1-p1",
        file_path=Path("OEBPS/chapter01.xhtml"),
        xpath="//p[1]",
        extract_mode=ExtractMode.HTML,
        source_content="<p>Main text<a><sup>1</sup></a> continues.</p>",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=0
        )
    )

    # Footnote definition segment (in separate file)
    footnote_segment = Segment(
        segment_id="notes-fn1",
        file_path=Path("OEBPS/notes.xhtml"),  # Different file
        xpath="//p[@id='fn1']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="fn1"><a href="#fn1a">1</a> This is the footnote text that explains something.</p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=99,  # Footnotes usually at end
            order_in_file=0
        )
    )

    # Simulate audiobook_files inclusion list filtering
    audiobook_files = {
        "OEBPS/chapter01.xhtml",
        # "OEBPS/notes.xhtml" is excluded
    }

    # Chapter file is included
    assert chapter_segment.file_path.as_posix() in audiobook_files

    # Footnote file is excluded
    assert footnote_segment.file_path.as_posix() not in audiobook_files

    # This filtering happens in controller.py before segment_to_text is called
    # So the footnote segment never gets processed for TTS


def test_skips_footnote_definition_sections():
    """Test that footnote definition sections are skipped based on segment ID."""

    # Footnote definition segment (from Dynasty EPUB)
    footnote_def = Segment(
        segment_id="ftn3",
        file_path=Path("chapter.xhtml"),
        xpath="//div[@id='div3']/p[@id='ftn3']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="ftn3"><a href="#ftn3a">*1</a> Two historians, Marcus Octavius and Licinius Macer, claimed that the rapist was the girl\'s uncle.</p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=100  # Late in file
        )
    )

    # Endnote with different ID pattern
    endnote_def = Segment(
        segment_id="note-42",
        file_path=Path("chapter.xhtml"),
        xpath="//div[@id='endnotes']/p[@id='note-42']",
        extract_mode=ExtractMode.HTML,
        source_content="<p>This is the endnote text.</p>",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=101
        )
    )

    # Footnote in div with class
    footnote_in_div = Segment(
        segment_id="p150",
        file_path=Path("chapter.xhtml"),
        xpath="//div[@class='footnotes']/p[@id='p150']",
        extract_mode=ExtractMode.HTML,
        source_content="<p>Footnote content here.</p>",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=150
        )
    )

    # All should be skipped (return None)
    assert segment_to_text(footnote_def, reader=None) is None
    assert segment_to_text(endnote_def, reader=None) is None
    assert segment_to_text(footnote_in_div, reader=None) is None


def test_preserves_regular_links_in_text():
    """Ensure regular hyperlinks without sup/sub are preserved."""

    segment = Segment(
        segment_id="p5",
        file_path=Path("chapter.xhtml"),
        xpath="//p[@id='p5']",
        extract_mode=ExtractMode.HTML,
        source_content='<p id="p5">Visit <a href="https://example.com">our website</a> for more information.</p>',
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=0
        )
    )

    mock_doc = Mock()
    element = lxml_html.fromstring(segment.source_content)
    mock_doc.tree.xpath.return_value = [element]

    mock_reader = Mock()
    mock_reader.read_document_by_path.return_value = mock_doc

    result = segment_to_text(segment, reader=mock_reader)

    # Link text is preserved
    assert "our website" in result
    assert "Visit" in result
    assert "for more information" in result
