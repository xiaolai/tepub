"""Tests for parallel translation processing."""

import threading
from pathlib import Path

import pytest
from rich.console import Console

from config import AppSettings
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument, SegmentStatus
from state.store import load_state, save_segments
from translation.controller import run_translation


class DummyParallelProvider:
    """Provider that records how many calls run at the same time."""

    name = "dummy"
    model = "dummy-model"

    def __init__(self, *, together: int | None = None):
        self.call_count = 0
        self.active = 0
        self.max_active = 0
        self._together = together
        self._lock = threading.Lock()
        # The first ``together`` calls block until all of them are running, so
        # the test fails unless the run really overlaps them. Later calls pass
        # straight through, since the remainder need not fill a barrier.
        self._barrier = threading.Barrier(together, timeout=10) if together else None

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        """Simulate translation with a counter and concurrency gauge."""
        with self._lock:
            self.call_count += 1
            number = self.call_count
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self._barrier is not None and number <= self._together:
                self._barrier.wait()
            return f"Translation {number}"
        finally:
            with self._lock:
                self.active -= 1


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
            xpath=f"/html/body/p[{i + 1}]",
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

    provider = DummyParallelProvider(together=3)
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
    for segment in segments:
        record = state.segments[segment.segment_id]
        assert record.status == SegmentStatus.COMPLETED
        assert record.translation is not None
        assert "Translation" in record.translation

    # Verify provider was called for each segment
    assert provider.call_count == 10
    # Three calls had to be running together for the barrier to release.
    assert provider.max_active == 3


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
    # One worker never runs two calls at once.
    assert provider.max_active == 1
