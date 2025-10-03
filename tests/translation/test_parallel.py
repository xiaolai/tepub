"""Tests for parallel translation processing."""
from pathlib import Path

import pytest
from rich.console import Console

from config import AppSettings
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument, SegmentStatus
from state.store import load_state, save_segments
from translation.controller import run_translation


class DummyParallelProvider:
    """Provider that simulates parallel translation."""

    name = "dummy"
    model = "dummy-model"

    def __init__(self):
        self.call_count = 0

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        """Simulate translation with a counter."""
        self.call_count += 1
        return f"<p>Translation {self.call_count}</p>"


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings().model_copy(update={"work_dir": tmp_path})
    cfg.ensure_directories()
    return cfg


def _write_segments(settings: AppSettings, input_epub: Path, count: int = 5) -> list[Segment]:
    """Create test segments."""
    segments = []
    for i in range(count):
        segment = Segment(
            segment_id=f"seg-{i:03d}",
            file_path=Path("Text/chapter1.xhtml"),
            xpath=f"/html/body/p[{i+1}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Text segment {i}",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i + 1),
        )
        segments.append(segment)

    document = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2024-01-01T00:00:00Z",
        segments=segments,
        skipped_documents=[],
    )
    save_segments(document, settings.segments_file)
    return segments


def test_parallel_translation_processes_all_segments(monkeypatch, settings, tmp_path):
    """Test that parallel translation processes all segments."""
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = _write_segments(settings, input_epub, count=10)

    provider = DummyParallelProvider()
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: provider)

    test_console = Console(record=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    # Set workers to 3 to test parallel execution
    settings.translation_workers = 3

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    # Verify all segments were translated
    state = load_state(settings.state_file)
    assert len(state.segments) == 10
    for i, segment in enumerate(segments):
        record = state.segments[segment.segment_id]
        assert record.status == SegmentStatus.COMPLETED
        assert record.translation is not None
        assert "Translation" in record.translation

    # Verify provider was called for each segment
    assert provider.call_count == 10


def test_parallel_with_single_worker_is_sequential(monkeypatch, settings, tmp_path):
    """Test that workers=1 still works (sequential mode)."""
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = _write_segments(settings, input_epub, count=5)

    provider = DummyParallelProvider()
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: provider)

    test_console = Console(record=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    # Set workers to 1 for sequential execution
    settings.translation_workers = 1

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    # Verify all segments were translated
    state = load_state(settings.state_file)
    assert len(state.segments) == 5
    for segment in segments:
        record = state.segments[segment.segment_id]
        assert record.status == SegmentStatus.COMPLETED
        assert record.translation is not None

    assert provider.call_count == 5
