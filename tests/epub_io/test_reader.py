from __future__ import annotations

from pathlib import Path

import pytest

from config import AppSettings
from epub_io.container import EpubStructureError
from epub_io.reader import MAX_EPUB_SIZE, EpubReader
from tests.epub_builder import build_epub


def test_epub_reader_rejects_missing_file(tmp_path):
    """Test that EpubReader raises FileNotFoundError for missing files."""
    settings = AppSettings(work_dir=tmp_path)
    missing_epub = tmp_path / "missing.epub"

    with pytest.raises(FileNotFoundError, match="EPUB file not found"):
        EpubReader(missing_epub, settings)


def test_epub_reader_rejects_oversized_file(tmp_path):
    """Test that EpubReader raises ValueError for files exceeding MAX_EPUB_SIZE."""
    settings = AppSettings(work_dir=tmp_path)
    large_epub = tmp_path / "large.epub"

    # Create a file larger than MAX_EPUB_SIZE
    with open(large_epub, "wb") as f:
        f.seek(MAX_EPUB_SIZE + 1024)  # 500MB + 1KB
        f.write(b"\0")

    with pytest.raises(ValueError, match="EPUB file too large"):
        EpubReader(large_epub, settings)


def test_epub_reader_accepts_reasonable_size(tmp_path):
    """A book within the size limit opens; the check must not reject it."""
    settings = AppSettings(work_dir=tmp_path)
    book = build_epub(tmp_path / "small.epub", [("c.xhtml", "C", "<p>One.</p>")])

    reader = EpubReader(book, settings)

    assert [document.path for document in reader.documents()] == [Path("c.xhtml")]


def test_epub_reader_rejects_a_file_that_is_not_a_zip(tmp_path):
    """Malformed archives fail with the structure error, not a size error."""
    settings = AppSettings(work_dir=tmp_path)
    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"not a zip archive")

    with pytest.raises(EpubStructureError, match="not an EPUB"):
        EpubReader(broken, settings)
