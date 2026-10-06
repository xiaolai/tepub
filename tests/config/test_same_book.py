"""A workspace belongs to a book by its content, not by where the file sits.

Moving or renaming the EPUB made translate and export refuse the workspace,
with no way to say it was the same book; a different book must still be refused.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from config import AppSettings
from config.workspace import assert_same_book
from exceptions import ArtifactMismatchError
from extraction.pipeline import run_extraction
from state.store import load_segments
from tests.epub_builder import build_epub


@pytest.fixture
def extracted(tmp_path: Path):
    book = build_epub(tmp_path / "book.epub", [("ch1.xhtml", "One", "<p>Text.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    return book, load_segments(settings.segments_file)


def test_extraction_records_the_book_digest(extracted) -> None:
    _book, segments = extracted
    assert segments.epub_sha256 and len(segments.epub_sha256) == 64


def test_a_moved_book_is_the_same_book(tmp_path: Path, extracted) -> None:
    book, segments = extracted
    moved = tmp_path / "elsewhere" / "renamed.epub"
    moved.parent.mkdir()
    shutil.move(book, moved)
    assert_same_book(segments, moved)  # no error


def test_a_different_book_is_refused(tmp_path: Path, extracted) -> None:
    _book, segments = extracted
    other = build_epub(tmp_path / "other.epub", [("ch1.xhtml", "One", "<p>Other.</p>")])
    with pytest.raises(ArtifactMismatchError):
        assert_same_book(segments, other)


def test_an_older_workspace_without_a_digest_still_compares_paths(
    tmp_path: Path, extracted
) -> None:
    book, segments = extracted
    legacy = segments.model_copy(update={"epub_sha256": None})
    assert_same_book(legacy, book)
    moved = tmp_path / "renamed.epub"
    shutil.copy(book, moved)
    with pytest.raises(ArtifactMismatchError, match="extract"):
        assert_same_book(legacy, moved)
