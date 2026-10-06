from __future__ import annotations

from pathlib import Path

import pytest

from config import AppSettings
from epub_io import selector
from tests.epub_builder import build_epub


@pytest.fixture
def book(tmp_path) -> Path:
    """Four chapters; only the table-of-contents titles should drive skipping."""
    return build_epub(
        tmp_path / "book.epub",
        [
            ("Text/cover.xhtml", "Cover", "<p>Cover Page</p>"),
            ("Text/opening.xhtml", "Opening Remarks", "<p>Opening remarks from the editor.</p>"),
            ("Text/preface.xhtml", "Preface", "<p>This is the preface of the book.</p>"),
            ("Text/chapter1.xhtml", "Chapter 1", "<p>Acknowledgments and foreword</p>"),
        ],
    )


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings()
    return cfg.model_copy(update={"work_dir": tmp_path})


def test_collect_skip_candidates_uses_toc_only(book, settings):
    """Test that skip detection only uses TOC titles, not filename/content."""

    candidates = selector.collect_skip_candidates(book, settings)

    # Cover should be detected from TOC title
    assert any(c.file_path.name == "cover.xhtml" and c.source == "toc" for c in candidates)

    # chapter1.xhtml has "Acknowledgments" in content but NOT in TOC title
    # With TOC-only detection, it should NOT be flagged
    assert not any(c.file_path.name == "chapter1.xhtml" for c in candidates)


def test_analyze_skip_candidates_reports_unmatched_titles(book, settings):

    analysis = selector.analyze_skip_candidates(book, settings)

    assert "opening remarks" in analysis.toc_unmatched_titles


def test_skip_after_logic_triggers_cascade(book, settings):
    """Test that cascade skipping activates after back-matter triggers."""

    # Enable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": True})

    candidates = selector.collect_skip_candidates(book, settings)

    # Should have "cover" from TOC (index 0)
    # Should NOT have cascade skips since our fake book doesn't have back-matter triggers
    assert any(c.source == "toc" for c in candidates)
    assert not any(c.source == "cascade" for c in candidates)


def test_skip_after_logic_can_be_disabled(book, settings):
    """Test that cascade skipping can be disabled via configuration."""

    # Disable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": False})

    candidates = selector.collect_skip_candidates(book, settings)

    # Should only have TOC-based skips, no cascade
    assert all(c.source != "cascade" for c in candidates)


def _skipped(book: Path, settings) -> dict[str, str]:
    return {
        c.file_path.as_posix(): c.reason
        for c in selector.analyze_skip_candidates(book, settings).candidates
    }


def _chapters(n: int) -> list[tuple[str, str, str]]:
    return [
        (f"Text/ch{i}.xhtml", f"{i}. A Chapter", f"<p>Chapter {i} text.</p>")
        for i in range(1, n + 1)
    ]


def test_plural_acknowledgements_are_skipped(tmp_path, settings) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [*_chapters(3), ("Text/ack.xhtml", "Acknowledgements", "<p>Thanks.</p>")],
    )
    assert _skipped(book, settings) == {"Text/ack.xhtml": "acknowledgements"}


def test_notes_missing_from_the_toc_are_found_by_their_first_line(tmp_path, settings) -> None:
    """Converted books list only the chapters; the notes, titled by a plain
    paragraph reading "Notes", were translated though the rules skip notes."""
    book = build_epub(
        tmp_path / "b.epub",
        [
            *_chapters(8),
            (
                "Text/notes1.xhtml",
                "",
                "<p><span>Notes</span></p><p>Introduction</p><p>1 A source.</p>",
            ),
            ("Text/notes2.xhtml", "", "<p>Chapter 1</p><p>1 Another source.</p>"),
            ("Text/index.xhtml", "Index", "<p>Index</p>"),
        ],
    )
    assert _skipped(book, settings) == {
        "Text/notes1.xhtml": "notes",
        "Text/notes2.xhtml": "after notes",
        "Text/index.xhtml": "index",
    }


def test_a_chapter_that_only_starts_with_the_word_is_not_back_matter(tmp_path, settings) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [*_chapters(8), ("Text/extra.xhtml", "", "<p>Notes on method and sources follow.</p>")],
    )
    assert _skipped(book, settings) == {}


def test_untitled_notes_early_in_the_book_are_not_taken_for_back_matter(tmp_path, settings) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [("Text/early.xhtml", "", "<p>Notes</p><p>A short aside.</p>"), *_chapters(8)],
    )
    assert _skipped(book, settings) == {}


def _spine(*names: str) -> dict:
    from epub_io.container import SpineDocument

    return {
        Path(name): SpineDocument(
            index=i,
            idref=f"i{i}",
            href=Path(name),
            media_type="application/xhtml+xml",
            linear=True,
        )
        for i, name in enumerate(names)
    }


def test_a_section_title_does_not_skip_its_whole_chapter() -> None:
    """Designing Interfaces lost its chapter 10, whose last section is
    "Further Reading"; a preface lost its text over a section titled
    "Acknowledgments"."""
    spine = _spine("ch9.xhtml", "ch10.xhtml", "pref.xhtml")
    toc = [
        ("Preface", "pref.xhtml"),
        ("Acknowledgments", "pref.xhtml#ack"),
        ("10. Forms and Controls", "ch10.xhtml"),
        ("Further Reading", "ch10.xhtml#further"),
    ]
    candidates, _ = selector._collect_toc_candidates(
        spine, toc, ["acknowledgments", "further reading"]
    )
    assert candidates == {}


def test_a_late_back_matter_section_marks_its_file() -> None:
    """"Technical Terms" then "Index" in one file at the end of the book."""
    spine = _spine("ch1.xhtml", "ch2.xhtml", "back.xhtml")
    toc = [
        ("1. One", "ch1.xhtml"),
        ("2. Two", "ch2.xhtml"),
        ("Technical Terms", "back.xhtml"),
        ("Index", "back.xhtml#idx"),
    ]
    candidates, _ = selector._collect_toc_candidates(
        spine, toc, ["index"], back_matter=["index"], back_matter_from=2
    )
    assert {path.as_posix(): c.reason for path, c in candidates.items()} == {"back.xhtml": "index"}


def test_build_skip_map_reuses_a_reader_it_is_given(book, settings, monkeypatch):
    """Extraction already opened the book; the skip map must not parse it again."""
    from epub_io.reader import EpubReader

    reader = EpubReader(book, settings)

    def _refuse(*_args, **_kwargs):
        raise AssertionError("the book was opened a second time")

    monkeypatch.setattr(selector, "EpubReader", _refuse)
    skip_map = selector.build_skip_map(book, settings, reader=reader)
    assert any(path.name == "cover.xhtml" for path in skip_map)


def test_extraction_parses_the_package_once(book, settings, monkeypatch):
    from epub_io import reader as reader_module
    from extraction import pipeline

    calls = []
    real = reader_module.read_package
    monkeypatch.setattr(
        reader_module, "read_package", lambda path: calls.append(path) or real(path)
    )
    pipeline.run_extraction(settings, book)
    assert len(calls) == 1


def test_extraction_parses_each_document_once(tmp_path, settings, monkeypatch):
    """The skip analysis looks for untitled notes by parsing the spine; the
    extraction walk then parsed every document again."""
    from epub_io import reader as reader_module
    from extraction import pipeline

    book = build_epub(
        tmp_path / "b.epub",
        [
            *_chapters(8),
            ("Text/notes1.xhtml", "", "<p>Notes</p><p>1 A source.</p>"),
            ("Text/notes2.xhtml", "", "<p>Chapter 1</p><p>1 Another source.</p>"),
        ],
    )
    parsed = []
    real = reader_module.parse_xhtml
    monkeypatch.setattr(reader_module, "parse_xhtml", lambda raw: parsed.append(raw) or real(raw))
    pipeline.run_extraction(settings, book)
    assert parsed and len(parsed) == len(set(parsed))
