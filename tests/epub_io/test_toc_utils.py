"""Tests for epub_io.toc_utils module."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from epub_io.toc_utils import parse_toc_to_dict


class MockLink:
    """Mock EPUB Link object."""

    def __init__(self, href: str, title: str):
        self.href = href
        self.title = title


def test_parse_toc_empty():
    """Test parsing empty TOC returns empty dict."""
    reader = Mock()
    reader.book.toc = []

    result = parse_toc_to_dict(reader)

    assert result == {}


def test_parse_toc_none():
    """Test parsing None TOC returns empty dict."""
    reader = Mock()
    reader.book.toc = None

    result = parse_toc_to_dict(reader)

    assert result == {}


def test_parse_toc_single_link():
    """Test parsing single TOC link."""
    reader = Mock()
    reader.book.toc = [MockLink("chapter1.xhtml", "Chapter 1")]

    result = parse_toc_to_dict(reader)

    assert result == {"chapter1.xhtml": "Chapter 1"}


def test_parse_toc_multiple_links():
    """Test parsing multiple TOC links."""
    reader = Mock()
    reader.book.toc = [
        MockLink("intro.xhtml", "Introduction"),
        MockLink("chapter1.xhtml", "Chapter 1"),
        MockLink("chapter2.xhtml", "Chapter 2"),
    ]

    result = parse_toc_to_dict(reader)

    assert result == {
        "intro.xhtml": "Introduction",
        "chapter1.xhtml": "Chapter 1",
        "chapter2.xhtml": "Chapter 2",
    }


def test_parse_toc_fragment_removal():
    """Test that fragments are removed from hrefs."""
    reader = Mock()
    reader.book.toc = [
        MockLink("chapter1.xhtml#section1", "Section 1"),
        MockLink("chapter1.xhtml#section2", "Section 2"),
        MockLink("chapter2.xhtml#intro", "Chapter 2 Intro"),
    ]

    result = parse_toc_to_dict(reader)

    # Multiple fragments for same file should keep last title
    assert "chapter1.xhtml" in result
    assert result["chapter2.xhtml"] == "Chapter 2 Intro"


def test_parse_toc_nested_tuple():
    """Test parsing nested tuple structure (older EpubPy format)."""
    reader = Mock()
    reader.book.toc = [
        (MockLink("part1.xhtml", "Part 1"), [
            MockLink("chapter1.xhtml", "Chapter 1"),
            MockLink("chapter2.xhtml", "Chapter 2"),
        ]),
    ]

    result = parse_toc_to_dict(reader)

    assert result == {
        "part1.xhtml": "Part 1",
        "chapter1.xhtml": "Chapter 1",
        "chapter2.xhtml": "Chapter 2",
    }


def test_parse_toc_deeply_nested():
    """Test parsing deeply nested TOC."""
    reader = Mock()
    reader.book.toc = [
        (MockLink("part1.xhtml", "Part 1"), [
            (MockLink("chapter1.xhtml", "Chapter 1"), [
                MockLink("section1.xhtml", "Section 1.1"),
                MockLink("section2.xhtml", "Section 1.2"),
            ]),
        ]),
    ]

    result = parse_toc_to_dict(reader)

    assert result == {
        "part1.xhtml": "Part 1",
        "chapter1.xhtml": "Chapter 1",
        "section1.xhtml": "Section 1.1",
        "section2.xhtml": "Section 1.2",
    }


def test_parse_toc_empty_title():
    """Test handling of empty titles."""
    reader = Mock()
    reader.book.toc = [
        MockLink("chapter1.xhtml", ""),
        MockLink("chapter2.xhtml", "Chapter 2"),
    ]

    result = parse_toc_to_dict(reader)

    assert result == {
        "chapter1.xhtml": "",
        "chapter2.xhtml": "Chapter 2",
    }


def test_parse_toc_none_title():
    """Test handling of None titles."""
    link = Mock()
    link.href = "chapter1.xhtml"
    link.title = None

    reader = Mock()
    reader.book.toc = [link]

    result = parse_toc_to_dict(reader)

    # None titles should be preserved as empty string
    assert "chapter1.xhtml" in result


def test_parse_toc_missing_attributes():
    """Test handling of items without href or title attributes."""
    invalid_item = Mock(spec=[])  # No href or title attributes

    reader = Mock()
    reader.book.toc = [
        MockLink("chapter1.xhtml", "Chapter 1"),
        invalid_item,
        MockLink("chapter2.xhtml", "Chapter 2"),
    ]

    result = parse_toc_to_dict(reader)

    # Should skip invalid item and process valid ones
    assert result == {
        "chapter1.xhtml": "Chapter 1",
        "chapter2.xhtml": "Chapter 2",
    }


def test_parse_toc_mixed_formats():
    """Test handling of mixed link and tuple formats."""
    reader = Mock()
    reader.book.toc = [
        MockLink("intro.xhtml", "Introduction"),
        (MockLink("part1.xhtml", "Part 1"), [
            MockLink("chapter1.xhtml", "Chapter 1"),
        ]),
        MockLink("epilogue.xhtml", "Epilogue"),
    ]

    result = parse_toc_to_dict(reader)

    assert result == {
        "intro.xhtml": "Introduction",
        "part1.xhtml": "Part 1",
        "chapter1.xhtml": "Chapter 1",
        "epilogue.xhtml": "Epilogue",
    }
