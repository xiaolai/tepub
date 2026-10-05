"""Footnote handling for narration, against a real EPUB rather than a Mock.

The previous tests mocked ``reader.read_document_by_path``. EpubReader had no
such method, so in production every lookup raised AttributeError inside a bare
``except`` and footnotes were narrated in full while the suite stayed green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook.preprocess import segment_to_text
from config import AppSettings
from epub_io.reader import EpubReader
from extraction.segments import iter_segments
from tests.epub_builder import build_epub

CHAPTER = """
<section epub:type="chapter">
  <h1>The Voyage</h1>
  <p id="body">The ship sailed at dawn<a epub:type="noteref" href="#fn1"><sup>1</sup></a> and the crew cheered.</p>
  <div class="note"><p>Note: this admonition is part of the text and must be read.</p></div>
  <aside epub:type="footnote" id="fn1"><p>Some sources say it was dusk.</p></aside>
  <div class="footnote"><p>An EPUB 2 style footnote body, marked only by its class.</p></div>
  <div id="div3"><p id="ftn3"><a href="#ftn3a">*1</a> Two historians claimed it was the uncle.</p></div>
  <div><p id="note-42">This is the endnote text.</p></div>
</section>
"""


@pytest.fixture
def book(tmp_path: Path):
    epub = build_epub(
        tmp_path / "book.epub",
        [
            ("ch1.xhtml", "The Voyage", CHAPTER),
            # File names that contain note-like words are ordinary chapters.
            ("authors_note.xhtml", "Author's Note", "<p>Thanks to everyone.</p>"),
            ("keynote.xhtml", "Keynote", "<p>The keynote speech began.</p>"),
        ],
    )
    reader = EpubReader(epub, AppSettings(work_dir=tmp_path / "work"))
    segments = []
    for document in reader.iter_documents():
        segments.extend(
            iter_segments(document.tree, document.path, document.spine_item.index)
        )
    return reader, segments


def _spoken(reader, segments) -> list[str]:
    texts = (segment_to_text(segment, reader=reader) for segment in segments)
    return [text for text in texts if text]


def test_note_reference_is_not_spoken(book) -> None:
    reader, segments = book
    spoken = _spoken(reader, segments)
    assert "The ship sailed at dawn and the crew cheered." in spoken


def test_footnote_bodies_are_not_spoken(book) -> None:
    reader, segments = book
    joined = " ".join(_spoken(reader, segments))
    assert "Some sources say it was dusk." not in joined
    assert "An EPUB 2 style footnote body" not in joined
    # Books that mark definitions only by id, such as ftn3 or note-42.
    assert "Two historians claimed it was the uncle." not in joined
    assert "This is the endnote text." not in joined


def test_admonition_with_class_note_is_spoken(book) -> None:
    reader, segments = book
    joined = " ".join(_spoken(reader, segments))
    assert "this admonition is part of the text" in joined


def test_files_named_like_notes_are_spoken(book) -> None:
    reader, segments = book
    joined = " ".join(_spoken(reader, segments))
    assert "Thanks to everyone." in joined
    assert "The keynote speech began." in joined


def test_reader_finds_a_document_by_path(book) -> None:
    reader, _ = book
    document = reader.read_document_by_path(Path("ch1.xhtml"))
    assert document.tree.xpath("//aside")
