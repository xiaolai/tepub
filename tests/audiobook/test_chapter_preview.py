"""The chapter preview lists exactly the chapters the audiobook will contain."""

from __future__ import annotations

from pathlib import Path

from audiobook.chapters import extract_chapters_from_epub
from config import AppSettings
from extraction.pipeline import run_extraction
from state.store import load_segments
from tests.epub_builder import build_epub


def _book(tmp_path: Path):
    book = build_epub(
        tmp_path / "b.epub",
        [
            ("cover.xhtml", "Cover", "<p>Cover words.</p>"),
            ("ch1.xhtml", "One", "<p>Chapter one.</p>"),
            ("notes.xhtml", "Notes", "<p>Endnotes.</p>"),
        ],
    )
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    return book, settings


def _titles(book, settings) -> list[str]:
    chapters, _meta = extract_chapters_from_epub(book, settings)
    return [chapter.title for chapter in chapters]


def test_skipped_files_are_not_previewed(tmp_path: Path) -> None:
    book, settings = _book(tmp_path)
    segments = load_segments(settings.segments_file).segments
    skipped = {s.file_path.name for s in segments if s.skip_reason}
    assert skipped, "the default skip rules should mark the cover and the notes"

    titles = {"cover.xhtml": "Cover", "ch1.xhtml": "One", "notes.xhtml": "Notes"}
    expected = [titles[name] for name in titles if name not in skipped]
    assert _titles(book, settings) == expected


def test_the_inclusion_list_is_honoured(tmp_path: Path) -> None:
    book, settings = _book(tmp_path)
    settings = settings.model_copy(update={"audiobook_files": ["ch1.xhtml"]})
    assert _titles(book, settings) == ["One"]
