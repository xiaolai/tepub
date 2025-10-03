"""Tests for epub_io.path_utils module."""

from __future__ import annotations

from pathlib import Path

import pytest

from epub_io.path_utils import normalize_epub_href


def test_normalize_empty_href():
    """Test that empty href returns None."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "")
    assert result is None


def test_normalize_whitespace_only():
    """Test that whitespace-only href returns None."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "   ")
    assert result is None


def test_normalize_none_href():
    """Test that None href returns None."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), None)
    assert result is None


def test_normalize_data_uri():
    """Test that data URIs are rejected."""
    result = normalize_epub_href(
        Path("text/chapter1.xhtml"),
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"
    )
    assert result is None


def test_normalize_http_url():
    """Test that HTTP URLs are rejected."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "http://example.com/image.jpg")
    assert result is None


def test_normalize_https_url():
    """Test that HTTPS URLs are rejected."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "https://example.com/image.jpg")
    assert result is None


def test_normalize_same_directory():
    """Test normalizing href in same directory."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "image.jpg")
    assert result == "text/image.jpg"


def test_normalize_subdirectory():
    """Test normalizing href in subdirectory."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "images/cover.jpg")
    assert result == "text/images/cover.jpg"


def test_normalize_parent_directory():
    """Test normalizing href in parent directory."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "../images/cover.jpg")
    assert result == "images/cover.jpg"


def test_normalize_root_relative():
    """Test normalizing absolute path (relative to EPUB root)."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "/images/cover.jpg")
    assert result == "images/cover.jpg"


def test_normalize_complex_relative():
    """Test normalizing complex relative path."""
    result = normalize_epub_href(
        Path("text/part1/chapter1.xhtml"),
        "../../images/diagrams/fig1.png"
    )
    assert result == "images/diagrams/fig1.png"


def test_normalize_dot_segments():
    """Test that . segments are normalized."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "./images/./cover.jpg")
    assert result == "text/images/cover.jpg"


def test_normalize_traversal_outside_root():
    """Test that paths traversing outside EPUB root are rejected."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "../../etc/passwd")
    assert result is None


def test_normalize_traversal_outside_root_complex():
    """Test rejection of complex traversal attempts."""
    result = normalize_epub_href(
        Path("text/chapter1.xhtml"),
        "../../../sensitive/data.txt"
    )
    assert result is None


def test_normalize_multiple_slashes():
    """Test that multiple slashes are normalized."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "images//cover.jpg")
    assert result == "text/images/cover.jpg"


def test_normalize_trailing_slash():
    """Test href with trailing slash."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "images/")
    assert result == "text/images"


def test_normalize_from_root_document():
    """Test normalizing from document at EPUB root."""
    result = normalize_epub_href(Path("index.xhtml"), "images/cover.jpg")
    assert result == "images/cover.jpg"


def test_normalize_deeply_nested_document():
    """Test normalizing from deeply nested document."""
    result = normalize_epub_href(
        Path("text/part1/section2/chapter3.xhtml"),
        "../../../images/cover.jpg"
    )
    assert result == "images/cover.jpg"


def test_normalize_preserves_extension():
    """Test that file extensions are preserved."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "diagram.svg")
    assert result == "text/diagram.svg"


def test_normalize_special_characters():
    """Test handling of special characters in filenames."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "image (1).jpg")
    assert result == "text/image (1).jpg"


def test_normalize_unicode_filename():
    """Test handling of Unicode characters in filenames."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "图片.jpg")
    assert result == "text/图片.jpg"


def test_normalize_windows_path_separator():
    """Test that backslashes are handled (though uncommon in EPUB)."""
    # Note: This may vary based on how PurePosixPath handles backslashes
    result = normalize_epub_href(Path("text/chapter1.xhtml"), r"images\cover.jpg")
    # Should still work as POSIX path
    assert result is not None


def test_normalize_leading_trailing_whitespace():
    """Test that leading/trailing whitespace is stripped."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "  image.jpg  ")
    assert result == "text/image.jpg"


def test_normalize_query_string():
    """Test href with query string (uncommon but possible)."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "image.jpg?version=2")
    # Query strings should be preserved in normalized path
    assert "image.jpg?version=2" in result


def test_normalize_anchor_fragment():
    """Test that anchor fragments are preserved."""
    result = normalize_epub_href(Path("text/chapter1.xhtml"), "chapter2.xhtml#section1")
    # Fragments should be preserved
    assert "chapter2.xhtml#section1" in result
