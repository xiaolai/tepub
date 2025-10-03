from pathlib import Path
from types import SimpleNamespace

import pytest
from ebooklib import epub

from extraction.markdown_export import (
    _sanitize_filename,
    _html_to_markdown,
    export_to_markdown,
    export_combined_markdown,
)
from epub_io.reader import EpubReader
from epub_io.toc_utils import parse_toc_to_dict
from epub_io.path_utils import normalize_epub_href
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument
from state.store import save_segments


def test_sanitize_filename():
    assert _sanitize_filename("Chapter 1: Introduction") == "chapter-1-introduction"
    assert _sanitize_filename("File/Path\\Test") == "filepathtest"
    assert _sanitize_filename("   Spaces   ") == "spaces"
    assert _sanitize_filename("A" * 100) == "a" * 50
    assert _sanitize_filename("") == "untitled"
    assert _sanitize_filename("!!!") == "untitled"


def test_normalize_image_href():
    assert normalize_epub_href(Path("Text/ch1.xhtml"), "../images/fig1.png") == "images/fig1.png"
    assert normalize_epub_href(Path("Text/ch1.xhtml"), "images/fig1.png") == "Text/images/fig1.png"
    assert normalize_epub_href(Path("Text/ch1.xhtml"), "/images/fig1.png") == "images/fig1.png"
    assert normalize_epub_href(Path("Text/ch1.xhtml"), "data:image/png;base64,abc") is None
    assert normalize_epub_href(Path("Text/ch1.xhtml"), "http://example.com/img.jpg") is None


def test_html_to_markdown_with_images():
    html = '<p>Text before <img src="../images/fig1.png" alt="Figure 1"/> text after</p>'
    image_mapping = {"images/fig1.png": "fig1.png"}
    result = _html_to_markdown(html, Path("Text/ch1.xhtml"), image_mapping)
    assert "![Figure 1](images/fig1.png)" in result
    assert "Text before" in result
    assert "text after" in result


def test_html_to_markdown_without_images():
    html = "<p>Hello <b>world</b></p>"
    result = _html_to_markdown(html, Path("Text/ch1.xhtml"), {})
    assert "Hello" in result
    assert "world" in result


def test_parse_toc_with_mock_reader(tmp_path, monkeypatch):
    """Test TOC parsing with a mock epub book."""
    # Create mock TOC structure
    link1 = epub.Link("Text/ch1.xhtml", "Chapter 1", "ch1")
    link2 = epub.Link("Text/ch2.xhtml#section", "Chapter 2", "ch2")
    nested = epub.Link("Text/ch3.xhtml", "Chapter 3", "ch3")

    mock_book = SimpleNamespace(toc=[link1, link2, (nested, [])])

    # Create mock reader
    mock_reader = SimpleNamespace(book=mock_book)

    toc_map = parse_toc_to_dict(mock_reader)

    assert toc_map["Text/ch1.xhtml"] == "Chapter 1"
    assert toc_map["Text/ch2.xhtml"] == "Chapter 2"
    assert toc_map["Text/ch3.xhtml"] == "Chapter 3"


def test_export_to_markdown(tmp_path, monkeypatch):
    """Test full markdown export with segments."""
    # Create test segments
    segments = [
        Segment(
            segment_id="seg-1",
            file_path=Path("Text/ch1.xhtml"),
            xpath="/html/body/p[1]",
            extract_mode=ExtractMode.TEXT,
            source_content="First paragraph in chapter 1.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
        ),
        Segment(
            segment_id="seg-2",
            file_path=Path("Text/ch1.xhtml"),
            xpath="/html/body/p[2]",
            extract_mode=ExtractMode.TEXT,
            source_content="Second paragraph in chapter 1.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=2),
        ),
        Segment(
            segment_id="seg-3",
            file_path=Path("Text/ch2.xhtml"),
            xpath="/html/body/p[1]",
            extract_mode=ExtractMode.TEXT,
            source_content="First paragraph in chapter 2.",
            metadata=SegmentMetadata(element_type="p", spine_index=1, order_in_file=1),
        ),
    ]

    # Save segments
    work_dir = tmp_path / ".tepub"
    work_dir.mkdir()
    segments_file = work_dir / "segments.json"

    segments_doc = SegmentsDocument(
        epub_path=tmp_path / "test.epub",
        generated_at="2024-01-01T00:00:00",
        segments=segments,
    )
    save_segments(segments_doc, segments_file)

    # Create mock settings
    settings = SimpleNamespace(segments_file=segments_file)

    # Mock TOC
    link1 = epub.Link("Text/ch1.xhtml", "Introduction", "ch1")
    link2 = epub.Link("Text/ch2.xhtml", "Chapter Two", "ch2")

    # Mock spine items
    mock_item1 = SimpleNamespace(id="ch1", file_name="Text/ch1.xhtml", media_type="application/xhtml+xml")
    mock_item2 = SimpleNamespace(id="ch2", file_name="Text/ch2.xhtml", media_type="application/xhtml+xml")
    mock_spine = [("ch1", "yes"), ("ch2", "yes")]

    mock_book = SimpleNamespace(
        toc=[link1, link2],
        spine=mock_spine
    )

    def mock_get_items():
        return [mock_item1, mock_item2]

    mock_book.get_items = mock_get_items

    # Mock EpubReader to return our mock book
    def mock_reader_init(self, epub_path, settings):
        self.epub_path = epub_path
        self.settings = settings
        self.book = mock_book

    monkeypatch.setattr(EpubReader, "__init__", mock_reader_init)

    # Export markdown
    mock_epub = tmp_path / "test.epub"
    output_dir = tmp_path / "markdown"
    created_files = export_to_markdown(settings, mock_epub, output_dir)

    # Verify output
    assert len(created_files) == 2
    assert created_files[0].name == "001_introduction.md"
    assert created_files[1].name == "002_chapter-two.md"

    # Check content
    content1 = created_files[0].read_text()
    assert "# Introduction" in content1
    assert "First paragraph in chapter 1." in content1
    assert "Second paragraph in chapter 1." in content1

    content2 = created_files[1].read_text()
    assert "# Chapter Two" in content2
    assert "First paragraph in chapter 2." in content2


def test_export_combined_markdown(tmp_path, monkeypatch):
    """Test combined markdown export."""
    # Create test segments
    segments = [
        Segment(
            segment_id="seg-1",
            file_path=Path("Text/ch1.xhtml"),
            xpath="/html/body/p[1]",
            extract_mode=ExtractMode.TEXT,
            source_content="First paragraph in chapter 1.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
        ),
        Segment(
            segment_id="seg-2",
            file_path=Path("Text/ch2.xhtml"),
            xpath="/html/body/p[1]",
            extract_mode=ExtractMode.TEXT,
            source_content="First paragraph in chapter 2.",
            metadata=SegmentMetadata(element_type="p", spine_index=1, order_in_file=1),
        ),
    ]

    # Save segments
    work_dir = tmp_path / ".tepub"
    work_dir.mkdir()
    segments_file = work_dir / "segments.json"

    segments_doc = SegmentsDocument(
        epub_path=tmp_path / "test-book.epub",
        generated_at="2024-01-01T00:00:00",
        segments=segments,
    )
    save_segments(segments_doc, segments_file)

    # Create mock settings
    settings = SimpleNamespace(segments_file=segments_file)

    # Mock TOC
    link1 = epub.Link("Text/ch1.xhtml", "Introduction", "ch1")
    link2 = epub.Link("Text/ch2.xhtml", "Chapter Two", "ch2")

    # Mock spine items
    mock_item1 = SimpleNamespace(id="ch1", file_name="Text/ch1.xhtml", media_type="application/xhtml+xml")
    mock_item2 = SimpleNamespace(id="ch2", file_name="Text/ch2.xhtml", media_type="application/xhtml+xml")
    mock_spine = [("ch1", "yes"), ("ch2", "yes")]

    mock_book = SimpleNamespace(
        toc=[link1, link2],
        spine=mock_spine
    )

    def mock_get_items():
        return [mock_item1, mock_item2]

    mock_book.get_items = mock_get_items

    # Mock EpubReader
    def mock_reader_init(self, epub_path, settings):
        self.epub_path = epub_path
        self.settings = settings
        self.book = mock_book

    monkeypatch.setattr(EpubReader, "__init__", mock_reader_init)

    # Export combined markdown
    mock_epub = tmp_path / "test-book.epub"
    output_dir = tmp_path / "markdown"
    combined_file = export_combined_markdown(settings, mock_epub, output_dir)

    # Verify combined file
    assert combined_file.name == "test-book.md"
    assert combined_file.exists()

    # Check content
    content = combined_file.read_text()
    assert "# " in content  # Book title
    assert "## Introduction" in content
    assert "## Chapter Two" in content
    assert "First paragraph in chapter 1." in content
    assert "First paragraph in chapter 2." in content
    assert "---" in content  # Chapter separator
