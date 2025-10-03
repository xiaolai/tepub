from __future__ import annotations

from pathlib import Path

import pytest

from config import AppSettings
from epub_io.reader import EpubReader, MAX_EPUB_SIZE


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
    """Test that EpubReader accepts files within size limit."""
    settings = AppSettings(work_dir=tmp_path)
    small_epub = tmp_path / "small.epub"

    # Create a minimal valid ZIP file (EPUB is a ZIP)
    # This will fail to load as an EPUB, but should pass size validation
    with open(small_epub, "wb") as f:
        # Minimal ZIP file structure
        f.write(b"PK\x03\x04")  # Local file header signature
        f.write(b"\x00" * 26)  # Rest of header (26 bytes)
        # Central directory
        f.write(b"PK\x01\x02")  # Central directory header
        f.write(b"\x00" * 42)  # Rest of CD header
        # End of central directory
        f.write(b"PK\x05\x06")  # EOCD signature
        f.write(b"\x00" * 18)  # Rest of EOCD

    # This will fail loading the book, but size validation should pass
    # We're only testing size validation here
    try:
        EpubReader(small_epub, settings)
    except Exception as e:
        # Expected: will fail to load as valid EPUB
        # But should not be a "file too large" error
        assert "too large" not in str(e).lower()
