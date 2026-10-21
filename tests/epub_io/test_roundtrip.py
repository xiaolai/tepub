"""Writing a book with nothing changed must give back the same book.

ebooklib rebuilds every content document's <head> (dropping stylesheet links),
moves every file into EPUB/, regenerates the navigation document and adds a
spine reference to an NCX that does not exist. A clean book came back failing
epubcheck. These tests hold the writer to the input, byte for byte.
"""

from __future__ import annotations

import collections
import os
import zipfile
from pathlib import Path

import pytest

from epub_io.writer import write_updated_epub
from tests import epubcheck
from tests.epub_fixtures import FIXTURES

epubcheck_required = pytest.mark.skipif(
    not epubcheck.AVAILABLE, reason="epubcheck not installed"
)


def _entries(path: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


@epubcheck_required
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixtures_are_valid_as_built(name: str, tmp_path: Path) -> None:
    book = FIXTURES[name](tmp_path / f"{name}.epub")
    assert epubcheck.errors(epubcheck.check(book)) == []


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_an_unchanged_book_round_trips_byte_for_byte(name: str, tmp_path: Path) -> None:
    book = FIXTURES[name](tmp_path / f"{name}.epub")
    out = tmp_path / "out.epub"

    write_updated_epub(book, out, {})

    assert _entries(out) == _entries(book)
    with zipfile.ZipFile(out) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED


CORPUS = os.environ.get("TEPUB_CORPUS_DIR")


def _corpus_books() -> list[Path]:
    if not CORPUS:
        return []
    return sorted(Path(CORPUS).expanduser().glob("*.epub"))


@epubcheck_required
@pytest.mark.corpus
@pytest.mark.skipif(not CORPUS, reason="set TEPUB_CORPUS_DIR to a folder of real EPUBs")
@pytest.mark.parametrize("book", _corpus_books(), ids=lambda p: p.stem[:40])
def test_real_books_gain_no_epubcheck_errors(book: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.epub"
    write_updated_epub(book, out, {})
    # An unchanged book must come back entry for entry. Comparing error kinds
    # as sets hid extra occurrences: a second broken link of a kind the source
    # already had looked identical.
    assert _entries(out) == _entries(book)
    before = epubcheck.check(book)
    after = epubcheck.check(out)
    assert collections.Counter(epubcheck.errors(after)) == collections.Counter(
        epubcheck.errors(before)
    )
    assert len(epubcheck.warnings(after)) <= len(epubcheck.warnings(before))
