from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from config import AppSettings, ProviderConfig
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus, SegmentsDocument
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
