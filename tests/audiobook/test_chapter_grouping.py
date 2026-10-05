"""No synthesised text is left out of the book because the TOC skips its file.

Files before the first table-of-contents entry, and after the last, were
narrated (and, with a paid voice, paid for) and then dropped from the book
without a word.
"""

from __future__ import annotations

from pathlib import Path

from audiobook.chapter_plan import _build_spine_to_toc_map, group_segments_into_chapters
from config import AppSettings
from epub_io.reader import EpubReader
from epub_io.toc_utils import parse_toc_to_dict
from extraction.segments import iter_segments
from tests.epub_builder import build_epub


def test_files_outside_the_toc_still_reach_a_chapter(tmp_path: Path) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [
            ("front.xhtml", "", "<p>A preface the TOC does not list.</p>"),
            ("ch1.xhtml", "One", "<p>Chapter one.</p>"),
            ("ch2.xhtml", "Two", "<p>Chapter two.</p>"),
            ("ch2b.xhtml", "", "<p>The rest of chapter two, split into another file.</p>"),
        ],
    )
    reader = EpubReader(book, AppSettings(work_dir=tmp_path / "w"))
    segments = [
        s for d in reader.iter_documents() for s in iter_segments(d.tree, d.path, d.spine_item.index)
    ]

    chapters = group_segments_into_chapters(
        segments, _build_spine_to_toc_map(reader, parse_toc_to_dict(reader))
    )

    grouped = {key: [s.file_path.as_posix() for s in members] for key, members in chapters}
    assert grouped == {
        "front.xhtml": ["front.xhtml"],
        "ch1.xhtml": ["ch1.xhtml"],
        "ch2.xhtml": ["ch2.xhtml", "ch2b.xhtml"],
    }
    assert [key for key, _ in chapters] == ["front.xhtml", "ch1.xhtml", "ch2.xhtml"]
