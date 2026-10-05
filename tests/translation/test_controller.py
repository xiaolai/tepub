from pathlib import Path

import pytest
from rich.console import Console

from config import AppSettings
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument, SegmentStatus
from state.store import load_state, save_segments
from translation.controller import run_translation
from translation.providers.base import ProviderFatalError


class DummyProvider:
    name = "dummy"
    model = "dummy-model"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        return "<p>Hola mundo</p>"


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings().model_copy(update={"work_dir": tmp_path})
    cfg.ensure_directories()
    return cfg


def _write_segments(settings: AppSettings, input_epub: Path) -> Segment:
    segment = Segment(
        segment_id="chapter1-001",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello world",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    document = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2024-01-01T00:00:00Z",
        segments=[segment],
        skipped_documents=[],
    )
    save_segments(document, settings.segments_file)
    return segment


def test_run_translation_updates_state(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)

    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())

    test_console = Console(record=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    record = state.segments[segment.segment_id]
    assert record.status == SegmentStatus.COMPLETED
    assert record.translation == "<p>Hola mundo</p>"
    assert record.provider_name == "dummy"
    assert record.model_name == "dummy-model"

    output = test_console.export_text()
    assert "Dashboard" in output
    assert "files" in output
    assert "Hola mundo" in output


class FailingProvider:
    name = "dummy"
    model = "dummy-model"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        raise ProviderFatalError("network unavailable")


def test_run_translation_handles_fatal_error(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "fatal.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)

    monkeypatch.setattr("translation.controller.create_provider", lambda _config: FailingProvider())

    test_console = Console(record=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    record = state.segments[segment.segment_id]
    assert record.status == SegmentStatus.ERROR

    # Error should be logged
    output = test_console.export_text()
    assert "network unavailable" in output.lower()


class SpyProvider:
    name = "spy"
    model = "spy-model"

    def __init__(self):
        self.calls = []

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        self.calls.append(segment.source_content)
        return "TRANSLATED"


def test_run_translation_auto_copies_punctuation(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "auto-copy.epub"
    input_epub.write_text("stub", encoding="utf-8")

    ellipsis_segment = Segment(
        segment_id="seg-ellipsis",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="…",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    content_segment = Segment(
        segment_id="seg-content",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[2]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello world",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=2),
    )

    document = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2024-01-01T00:00:00Z",
        segments=[ellipsis_segment, content_segment],
        skipped_documents=[],
    )
    save_segments(document, settings.segments_file)

    spy = SpyProvider()
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: spy)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    ellipsis_record = state.segments[ellipsis_segment.segment_id]
    assert ellipsis_record.status == SegmentStatus.COMPLETED
    assert ellipsis_record.translation == "…"
    assert ellipsis_record.provider_name is None  # Auto-copied segments have no provider
    assert ellipsis_record.model_name is None

    content_record = state.segments[content_segment.segment_id]
    assert content_record.provider_name == "spy"  # AI-translated segments have provider info
    assert content_record.model_name == "spy-model"

    assert spy.calls == ["Hello world"]


class FlakyProvider:
    """Fails the first three calls the way a rate-limited API does, then works."""

    name = "dummy"
    model = "dummy-model"

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        from translation.providers.base import ProviderError

        self.calls += 1
        if self.calls <= 3:
            raise ProviderError("429 rate limited")
        return "Hola"


def test_a_cooldown_resumes_the_run(monkeypatch, settings, tmp_path):
    """After a cooldown the run carries on; it used to wait 30 minutes and quit."""
    from state.models import SegmentsDocument

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = [
        Segment(
            segment_id=f"c-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence number {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, 6)
    ]
    save_segments(
        SegmentsDocument(epub_path=input_epub, generated_at="2024-01-01T00:00:00Z", segments=segments),
        settings.segments_file,
    )
    settings = settings.model_copy(update={"translation_workers": 1})
    provider = FlakyProvider()
    slept: list[float] = []
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: provider)
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    state = load_state(settings.state_file)
    assert {r.status for r in state.segments.values()} == {SegmentStatus.COMPLETED}
    assert slept, "a cooldown should have been taken"


class AlwaysRateLimited:
    name = "dummy"
    model = "dummy-model"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        from translation.providers.base import ProviderError

        raise ProviderError("429 rate limited")


def test_cooldowns_are_capped(monkeypatch, settings, tmp_path):
    """A provider that never recovers ends the run after a bounded wait."""
    from translation import controller

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _write_segments(settings, input_epub)
    settings = settings.model_copy(update={"translation_workers": 1})
    slept: list[float] = []
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: AlwaysRateLimited())
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)
    monkeypatch.setattr("translation.controller.FAILURES_BEFORE_COOLDOWN", 1)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    expected = controller.MAX_COOLDOWNS_PER_RUN * controller.COOLDOWN.total_seconds()
    assert sum(slept) == expected
    state = load_state(settings.state_file)
    assert {r.status for r in state.segments.values()} == {SegmentStatus.ERROR}
