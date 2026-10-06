"""Every export runs on every fixture book (WI-4.7).

They used to read the book through ebooklib, XPath and text_content(), none of
which survive XHTML trees with namespaces; this proves the move is complete.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from audiobook.preprocess import segment_to_text
from config import AppSettings
from epub_io.reader import EpubReader
from extraction.markdown_export import export_combined_markdown, export_to_markdown
from extraction.pipeline import run_extraction
from state.store import load_segments
from tests.epub_fixtures import FIXTURES
from webbuilder import export_web


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_all_exports_run(name: str, tmp_path: Path) -> None:
    book = FIXTURES[name](tmp_path / f"{name}.epub")
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    segments = load_segments(settings.segments_file).segments
    assert segments, "nothing was extracted"

    markdown = tmp_path / "md"
    assert export_to_markdown(settings, book, markdown)
    assert export_combined_markdown(settings, book, markdown).read_text(encoding="utf-8")

    reader = EpubReader(book, settings)
    spoken = [segment_to_text(segment, reader=reader) for segment in segments]
    assert any(spoken)

    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    for segment in segments:
        state["segments"][segment.segment_id].update(
            translation=segment.source_content, provider_name="fake", status="completed"
        )
    settings.state_file.write_text(json.dumps(state), encoding="utf-8")
    web = export_web(settings, book, output_dir=tmp_path / "web").site
    assert (web / "index.html").exists()
