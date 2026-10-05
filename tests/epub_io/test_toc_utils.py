"""Tests for epub_io.toc_utils: document path to title, from the package's contents."""

from __future__ import annotations

from types import SimpleNamespace

from epub_io.container import TocEntry
from epub_io.toc_utils import parse_toc_to_dict


def _reader(*entries: TocEntry):
    """A reader carrying only a table of contents, which is plain data."""
    return SimpleNamespace(package=SimpleNamespace(toc=list(entries)))


def test_parse_toc_empty():
    assert parse_toc_to_dict(_reader()) == {}


def test_parse_toc_links():
    result = parse_toc_to_dict(
        _reader(
            TocEntry("Introduction", "intro.xhtml"),
            TocEntry("Chapter 1", "chapter1.xhtml"),
            TocEntry("Chapter 2", "chapter2.xhtml"),
        )
    )
    assert result == {
        "intro.xhtml": "Introduction",
        "chapter1.xhtml": "Chapter 1",
        "chapter2.xhtml": "Chapter 2",
    }


def test_fragments_are_removed_and_the_first_title_wins():
    """The first entry introduces a document; later sub-sections must not rename it."""
    result = parse_toc_to_dict(
        _reader(
            TocEntry("Section 1", "chapter1.xhtml#section1"),
            TocEntry("Section 2", "chapter1.xhtml#section2"),
            TocEntry("Chapter 2 Intro", "chapter2.xhtml#intro"),
        )
    )
    assert result == {"chapter1.xhtml": "Section 1", "chapter2.xhtml": "Chapter 2 Intro"}


def test_nested_entries_are_all_read():
    result = parse_toc_to_dict(
        _reader(
            TocEntry(
                "Part 1",
                "part1.xhtml",
                [
                    TocEntry(
                        "Chapter 1",
                        "chapter1.xhtml",
                        [TocEntry("Section 1.1", "section1.xhtml"), TocEntry("Section 1.2", "section2.xhtml")],
                    )
                ],
            ),
            TocEntry("Epilogue", "epilogue.xhtml"),
        )
    )
    assert result == {
        "part1.xhtml": "Part 1",
        "chapter1.xhtml": "Chapter 1",
        "section1.xhtml": "Section 1.1",
        "section2.xhtml": "Section 1.2",
        "epilogue.xhtml": "Epilogue",
    }


def test_an_empty_title_registers_the_document_without_a_title():
    result = parse_toc_to_dict(
        _reader(TocEntry("", "chapter1.xhtml"), TocEntry("Chapter 2", "chapter2.xhtml"))
    )
    assert result == {"chapter1.xhtml": "", "chapter2.xhtml": "Chapter 2"}


def test_a_heading_without_a_link_is_skipped_but_its_children_are_read():
    result = parse_toc_to_dict(
        _reader(TocEntry("Part One", None, [TocEntry("Chapter 1", "chapter1.xhtml")]))
    )
    assert result == {"chapter1.xhtml": "Chapter 1"}
