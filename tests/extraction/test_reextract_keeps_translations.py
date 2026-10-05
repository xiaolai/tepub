"""Re-running extract on a translated book keeps its translations.

Translate records language codes ("zh-CN"); extract passed the configured
names ("Simplified Chinese"), and the mismatch rebuilt the state as PENDING,
erasing a translated book, including right after a format upgrade had carried
every translation over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import AppSettings
from extraction.pipeline import run_extraction
from state.models import SegmentStatus
from state.store import load_state, save_state
from tests.epub_builder import build_epub


@pytest.mark.parametrize("format_upgrade", [False, True])
def test_reextracting_a_translated_book_keeps_every_translation(tmp_path: Path, format_upgrade: bool) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work", target_language="Simplified Chinese")
    run_extraction(settings, book)
    state = load_state(settings.state_file)
    for segment_id, record in state.segments.items():
        state.segments[segment_id] = record.model_copy(
            update={"translation": "译", "status": SegmentStatus.COMPLETED, "provider_name": "ollama"}
        )
    state.source_language, state.target_language = "auto", "zh-CN"  # as translate records them
    save_state(state, settings.state_file)
    if format_upgrade:
        data = json.loads(settings.segments_file.read_text(encoding="utf-8"))
        data["format_version"] = 3
        settings.segments_file.write_text(json.dumps(data), encoding="utf-8")

    run_extraction(settings, book)

    after = load_state(settings.state_file)
    assert len(after.segments) == 2
    assert {r.status for r in after.segments.values()} == {SegmentStatus.COMPLETED}
    assert {r.translation for r in after.segments.values()} == {"译"}
