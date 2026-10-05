"""Book metadata and chapter markers written into the finished audiobook."""

from __future__ import annotations

import logging
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover

from console_singleton import get_console
from epub_io.reader import EpubReader

from .mp4chapters import write_chapter_markers

logger = logging.getLogger(__name__)
console = get_console()



def _book_title(reader: EpubReader) -> str:
    return reader.package.metadata.title or reader.epub_path.stem

def _book_authors(reader: EpubReader) -> list[str]:
    return list(reader.package.metadata.creators)

def _tag_audiobook(
    workspace_path: Path,
    book_title: str,
    authors: list[str],
    cover_path: Path | None,
    chapter_markers: list[tuple[int, str]],
) -> None:
    """Write title/author/cover metadata and chapter markers to the final file."""
    mp4 = MP4(workspace_path)
    mp4["©nam"] = [book_title]
    mp4["©alb"] = [book_title]
    if authors:
        mp4["©ART"] = [", ".join(authors)]
    if cover_path and cover_path.exists():
        cover_bytes = cover_path.read_bytes()
        cover_img_format = (
            MP4Cover.FORMAT_PNG if cover_path.suffix.lower() == ".png" else MP4Cover.FORMAT_JPEG
        )
        mp4["covr"] = [MP4Cover(cover_bytes, imageformat=cover_img_format)]

    mp4.save()

    # mutagen reads chapters but cannot write them, so they are written as a Nero
    # chpl atom. A native-chapter branch here imported MP4Chapter, which mutagen
    # does not have, so it never ran.
    if chapter_markers:
        try:
            write_chapter_markers(workspace_path, chapter_markers)
        except Exception as exc:
            # Logging and carrying on reported a finished audiobook whose chapters
            # were missing or pointed at the wrong times.
            raise RuntimeError(
                f"The audio was written to {workspace_path}, but its chapter markers "
                f"could not be written correctly: {exc}"
            ) from exc
