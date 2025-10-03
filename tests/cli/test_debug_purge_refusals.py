from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from config import AppSettings
from state.models import (
    Segment,
    SegmentMetadata,
    ExtractMode,
    SegmentsDocument,
    StateDocument,
    TranslationRecord,
    SegmentStatus,
)
from state.store import save_segments, save_state, load_state


def _build_segment(segment_id: str, content: str) -> Segment:
    return Segment(
        segment_id=segment_id,
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content=content,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=0),
    )


def test_purge_refusals_resets_segments(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    settings = AppSettings(work_root=workspace, work_dir=workspace, cache_dir=workspace / "cache")
    settings.ensure_directories()

    segments = [
        _build_segment("seg-1", "1"),
        _build_segment("seg-2", "正常内容"),
    ]

    save_segments(
        SegmentsDocument(
            epub_path=tmp_path / "book.epub",
            generated_at="2025-09-27T00:00:00Z",
            segments=segments,
        ),
        settings.segments_file,
    )

    save_state(
        StateDocument(
            segments={
                "seg-1": TranslationRecord(
                    segment_id="seg-1",
                    translation="I'm sorry, I can't help with that.",
                    status=SegmentStatus.COMPLETED,
                    provider_name="openai",
                    model_name="gpt-4o",
                ),
                "seg-2": TranslationRecord(
                    segment_id="seg-2",
                    translation="正常翻译",
                    status=SegmentStatus.COMPLETED,
                ),
            }
        ),
        settings.state_file,
    )

    monkeypatch.setattr("cli.core.load_settings_from_cli", lambda path=None: settings)

    runner = CliRunner()
    result = runner.invoke(app, ["debug", "purge-refusals"])

    assert result.exit_code == 0
    state = load_state(settings.state_file)
    assert state.segments["seg-1"].status == SegmentStatus.PENDING
    assert state.segments["seg-1"].translation is None
    assert state.segments["seg-2"].status == SegmentStatus.COMPLETED


def test_purge_refusals_dry_run(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    settings = AppSettings(work_root=workspace, work_dir=workspace, cache_dir=workspace / "cache")
    settings.ensure_directories()

    segments = [_build_segment("seg-1", "1")]

    save_segments(
        SegmentsDocument(
            epub_path=tmp_path / "book.epub",
            generated_at="2025-09-27T00:00:00Z",
            segments=segments,
        ),
        settings.segments_file,
    )

    save_state(
        StateDocument(
            segments={
                "seg-1": TranslationRecord(
                    segment_id="seg-1",
                    translation="抱歉，我无法协助处理该内容。",
                    status=SegmentStatus.COMPLETED,
                ),
            }
        ),
        settings.state_file,
    )

    monkeypatch.setattr("cli.core.load_settings_from_cli", lambda path=None: settings)

    runner = CliRunner()
    result = runner.invoke(app, ["debug", "purge-refusals", "--dry-run"])

    assert result.exit_code == 0
    state = load_state(settings.state_file)
    assert state.segments["seg-1"].status == SegmentStatus.COMPLETED
