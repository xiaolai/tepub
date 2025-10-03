from __future__ import annotations

from pathlib import Path

from rich.console import Console

from config import AppSettings
from debug_tools import skip_lists
from state.models import SegmentsDocument, SkippedDocument, StateDocument


def test_show_skip_list_displays_auto_skips(monkeypatch, tmp_path) -> None:
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)

    doc = SegmentsDocument(
        epub_path=Path("book.epub"),
        generated_at="2025-01-01T00:00:00Z",
        segments=[],
        skipped_documents=[
            SkippedDocument(
                file_path=Path("Text/front.xhtml"),
                reason="cover",
                source="toc",
            )
        ],
    )
    state = StateDocument(segments={})

    test_console = Console(record=True)

    monkeypatch.setattr(skip_lists, "console", test_console)
    monkeypatch.setattr(skip_lists, "load_all_segments", lambda _settings: doc)
    monkeypatch.setattr(skip_lists, "load_translation_state", lambda _settings: state)

    skip_lists.show_skip_list(settings)

    output = test_console.export_text()
    assert "Automatically Skipped" in output
    assert "Text/front.xhtml" in output
    assert "toc" in output
