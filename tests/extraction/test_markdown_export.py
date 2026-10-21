from pathlib import Path
from types import SimpleNamespace

import pytest

from epub_io.path_utils import normalize_epub_href
from epub_io.toc_utils import parse_toc_to_dict
from extraction.markdown_export import (
    ChapterBlock,
    _html_to_markdown,
    _render_block_body,
    _sanitize_filename,
    export_combined_markdown,
    export_markdown,
    export_to_markdown,
)
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument
from state.store import save_segments
from tests.epub_builder import build_epub


def _html_segment(content: str, element_type: str) -> Segment:
    return Segment(
        segment_id="seg-1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/block",
        extract_mode=ExtractMode.HTML,
        source_content=content,
        metadata=SegmentMetadata(element_type=element_type, spine_index=0, order_in_file=1),
    )


def _rendered(segment: Segment) -> str:
    block = ChapterBlock("One", 0, 1, [Path("c.xhtml")], "c.xhtml")
    return "\n".join(_render_block_body(block, {Path("c.xhtml"): [segment]}, {}))


def test_plain_text_segments_are_not_parsed_as_html():
    """Parsed as HTML, "Use <foo> &copy;" lost <foo> and changed &copy;."""
    segment = _html_segment("Use <foo> &copy; here", "p")
    segment.extract_mode = ExtractMode.TEXT

    assert "Use <foo> &copy; here" in _rendered(segment)


def test_an_ordered_list_keeps_its_numbers():
    """Without its <ol> wrapper, an ordered list became bullets."""
    rendered = _rendered(_html_segment("<li>One</li><li>Two</li>", "ol"))

    assert "1. One" in rendered and "2. Two" in rendered


def test_a_table_keeps_its_header_separator():
    """Without its <table> wrapper, the header row was body cells."""
    segment = _html_segment(
        "<thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td></tr></tbody>",
        "table",
    )

    assert "---|---" in _rendered(segment)


def test_svg_image_references_reach_the_markdown():
    """html2text drops <image>, and nothing after it could bring it back."""
    html = (
        '<div><svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<image xlink:href="../images/fig1.png"/></svg></div>'
    )

    result = _html_to_markdown(html, Path("Text/ch1.xhtml"), {"images/fig1.png": "fig1.png"})

    assert "](images/fig1.png)" in result


@pytest.mark.parametrize(
    ("source", "extracted", "destination"),
    [
        ("fig 1.png", "fig 1.png", "images/fig%201.png"),
        ("f(1).png", "f(1).png", "images/f%281%29.png"),
        ("f%231.png", "f#1.png", "images/f%231.png"),
    ],
    ids=["space", "parentheses", "hash"],
)
def test_extracted_image_names_are_percent_encoded(source, extracted, destination):
    """An unescaped destination broke at the space, parenthesis or hash."""
    html = f'<p><img src="../images/{source}" alt="F"/></p>'

    result = _html_to_markdown(
        html, Path("Text/ch1.xhtml"), {f"images/{extracted}": extracted}
    )

    assert f"]({destination})" in result


def test_local_document_links_point_at_the_exported_chapter_files():
    """A link kept its XHTML destination, a file the export never creates."""
    block = ChapterBlock("One", 0, 1, [Path("Text/ch1.xhtml")], "ch1.xhtml")
    segment = _html_segment('<p>See <a href="ch2.xhtml#note1">the note</a>.</p>', "p")

    rendered = "\n".join(
        _render_block_body(
            block,
            {Path("Text/ch1.xhtml"): [segment]},
            {},
            {"Text/ch2.xhtml": "002_two.md"},
        )
    )

    assert "002_two.md#note1" in rendered


def test_ids_in_a_segment_become_markdown_anchors():
    """Links land on generated anchors; html2text drops the id attribute."""
    rendered = _rendered(_html_segment('<p id="note1">A note.</p>', "p"))

    assert '<a id="note1"></a>' in rendered


def test_combined_document_links_use_anchors_in_the_same_file(tmp_path):
    """The combined export writes one file, so its links must stay inside it."""
    from config import AppSettings
    from extraction.pipeline import run_extraction

    epub = build_epub(
        tmp_path / "book.epub",
        [
            (
                "Text/ch1.xhtml",
                "Introduction",
                '<p>See <a href="ch2.xhtml#note1">the note</a> or '
                '<a href="ch2.xhtml">chapter two</a>.</p>',
            ),
            ("Text/ch2.xhtml", "Second", '<p id="note1">A note.</p>'),
        ],
    )
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, epub)

    combined = export_combined_markdown(settings, epub, tmp_path / "markdown")
    content = combined.read_text()

    assert "the note" in content and "(<#note1>)" in content
    assert "chapter two" in content and "(<#tepub-chapter-2>)" in content
    assert '<a id="note1"></a>' in content
    assert '<a id="tepub-chapter-2"></a>' in content


def test_a_combined_file_does_not_overwrite_a_chapter_of_the_same_name(tmp_path):
    """A book named 001_introduction.epub overwrote its first chapter file."""
    epub = build_epub(
        tmp_path / "001_introduction.epub",
        [("Text/ch1.xhtml", "Introduction", "<p>1</p>")],
    )
    segments_file = tmp_path / "segments.json"
    save_segments(
        SegmentsDocument(
            epub_path=epub,
            generated_at="2024-01-01T00:00:00",
            segments=[
                Segment(
                    segment_id="seg-1",
                    file_path=Path("Text/ch1.xhtml"),
                    xpath="/html/body/p[1]",
                    extract_mode=ExtractMode.TEXT,
                    source_content="First paragraph.",
                    metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
                )
            ],
        ),
        segments_file,
    )
    settings = SimpleNamespace(segments_file=segments_file)
    output_dir = tmp_path / "markdown"

    chapters, combined = export_markdown(settings, epub, output_dir)

    assert [path.name for path in chapters] == ["001_introduction.md"]
    assert combined.name == "001_introduction-combined.md"
    assert chapters[0].read_text().startswith("# Introduction")
    assert "## Introduction" in combined.read_text()


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
