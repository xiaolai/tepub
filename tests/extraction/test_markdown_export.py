from pathlib import Path
from types import SimpleNamespace

from epub_io.path_utils import normalize_epub_href
from epub_io.toc_utils import parse_toc_to_dict
from extraction.markdown_export import (
    _html_to_markdown,
    _sanitize_filename,
    export_combined_markdown,
    export_to_markdown,
)
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument
from state.store import save_segments
from tests.epub_builder import build_epub


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


def test_parse_toc_maps_documents_to_their_first_title(tmp_path):
    from config import AppSettings
    from epub_io.reader import EpubReader

    book = build_epub(
        tmp_path / "b.epub",
        [
            ("Text/ch1.xhtml", "Chapter 1", "<p>1</p>"),
            ("Text/ch2.xhtml", "Chapter 2", "<p>2</p>"),
            ("Text/ch3.xhtml", "Chapter 3", "<p>3</p>"),
        ],
    )
    toc_map = parse_toc_to_dict(EpubReader(book, AppSettings(work_dir=tmp_path / "w")))

    assert toc_map == {
        "Text/ch1.xhtml": "Chapter 1",
        "Text/ch2.xhtml": "Chapter 2",
        "Text/ch3.xhtml": "Chapter 3",
    }


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

    build_epub(
        tmp_path / "test.epub",
        [
            ("Text/ch1.xhtml", "Introduction", "<p>1</p>"),
            ("Text/ch2.xhtml", "Chapter Two", "<p>2</p>"),
        ],
    )

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

    build_epub(
        tmp_path / "test-book.epub",
        [
            ("Text/ch1.xhtml", "Introduction", "<p>1</p>"),
            ("Text/ch2.xhtml", "Chapter Two", "<p>2</p>"),
        ],
    )

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
