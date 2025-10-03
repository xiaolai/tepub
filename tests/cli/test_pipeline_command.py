from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from config import AppSettings
from state.models import (
    ExtractMode,
    Segment,
    SegmentMetadata,
    SegmentStatus,
    SegmentsDocument,
    StateDocument,
    TranslationRecord,
)
from state.store import save_segments, save_state


def test_pipeline_runs_full_flow(monkeypatch, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")

    calls = []

    monkeypatch.setattr("cli.commands.pipeline.run_extraction", lambda **kwargs: calls.append("extract"))
    monkeypatch.setattr(
        "cli.commands.pipeline.run_translation",
        lambda **kwargs: calls.append(("translate", kwargs.get("source_language"), kwargs.get("target_language"))),
    )

    # Prepare settings with proper work_dir (exports go to workspace)
    work_dir = tmp_path / ".tepub"
    work_dir.mkdir(parents=True, exist_ok=True)

    def fake_prepare(ctx, s, i, override):
        return s.model_copy(update={"work_dir": work_dir, "work_root": work_dir})

    monkeypatch.setattr("cli.core.prepare_settings_for_epub", fake_prepare)

    def fake_export_web(settings, input_epub):
        web_path = input_epub.with_name(f"{input_epub.stem}_web")
        calls.append(("web", web_path))
        return web_path

    def fake_run_injection(  # type: ignore[override]
        settings,
        input_epub,
        output_epub,
        mode="bilingual",
    ):
        calls.append(("export", output_epub, mode))
        return ({Path("dummy.xhtml"): b"data"}, {})

    monkeypatch.setattr("cli.commands.export.export_web", lambda settings, input_epub, **kwargs: fake_export_web(settings, input_epub))
    archives = []

    def fake_create_web_archive(path):
        archives.append(path)
        return path.with_suffix(".zip")

    monkeypatch.setattr("cli.commands.export.run_injection", fake_run_injection)
    monkeypatch.setattr("cli.core.create_web_archive", fake_create_web_archive)

    runner = CliRunner()
    result = runner.invoke(app, ["pipeline", str(input_epub), "--from", "en", "--to", "Simplified Chinese"])

    assert result.exit_code == 0
    assert calls[0] == "extract"
    assert calls[1][0] == "translate"
    assert calls[1][1] == "en"
    assert calls[2][0] == "web"
    # EPUBs now export to workspace directory
    assert calls[3] == ("export", work_dir / f"{input_epub.stem}_bilingual{input_epub.suffix}", "bilingual")
    assert calls[4] == ("export", work_dir / f"{input_epub.stem}_translated{input_epub.suffix}", "translated_only")
    assert archives == [input_epub.with_name(f"{input_epub.stem}_web")]


def test_pipeline_web_only(monkeypatch, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")

    called = {"web": False, "epub": False}

    monkeypatch.setattr("cli.commands.pipeline.run_extraction", lambda **_: None)
    monkeypatch.setattr("cli.commands.pipeline.run_translation", lambda **_: None)
    monkeypatch.setattr("cli.core.prepare_settings_for_epub", lambda ctx, s, i, override: s)

    def fake_export_web(settings, input_epub):
        called["web"] = True
        return input_epub.with_name(f"{input_epub.stem}_web")

    def fake_run_injection(  # type: ignore[override]
        settings,
        input_epub,
        output_epub,
        mode="bilingual",
    ):
        called["epub"] = True
        return ({Path("dummy.xhtml"): b"data"}, {})

    monkeypatch.setattr("cli.commands.export.export_web", lambda settings, input_epub, **kwargs: fake_export_web(settings, input_epub))
    monkeypatch.setattr("cli.commands.export.run_injection", fake_run_injection)
    monkeypatch.setattr("cli.core.create_web_archive", lambda path: path.with_suffix(".zip"))

    runner = CliRunner()
    result = runner.invoke(app, ["pipeline", str(input_epub), "--from", "en", "--to", "Simplified Chinese", "--web"])

    assert result.exit_code == 0
    assert called["web"]
    assert not called["epub"]
    assert "Web version exported" in result.output
    assert "Wrote translated EPUB" not in result.output


def test_pipeline_epub_only_as_default(monkeypatch, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")

    called = {"web": False, "epub": False}

    monkeypatch.setattr("cli.commands.pipeline.run_extraction", lambda **kwargs: called.setdefault("extract", True))
    monkeypatch.setattr(
        "cli.commands.pipeline.run_translation",
        lambda **kwargs: called.setdefault("translate", True),
    )
    monkeypatch.setattr("cli.core.prepare_settings_for_epub", lambda ctx, s, i, override: s)

    def fake_export_web(settings, input_epub):
        called["web"] = True
        return input_epub

    def fake_run_injection(  # type: ignore[override]
        settings,
        input_epub,
        output_epub,
        mode="bilingual",
    ):
        called["epub"] = True
        return ({Path("dummy.xhtml"): b"data"}, {})

    monkeypatch.setattr("cli.commands.export.export_web", lambda settings, input_epub, **kwargs: fake_export_web(settings, input_epub))
    monkeypatch.setattr("cli.commands.export.run_injection", fake_run_injection)
    monkeypatch.setattr("cli.core.create_web_archive", lambda path: path.with_suffix(".zip"))

    runner = CliRunner()
    result = runner.invoke(app, [str(input_epub), "--to", "Simplified Chinese", "--epub"])

    assert result.exit_code == 0
    assert called["epub"]
    assert not called["web"]


def test_pipeline_resumes_existing_artifacts(monkeypatch, tmp_path):
    input_epub = tmp_path / "resume.epub"
    input_epub.write_text("stub", encoding="utf-8")

    workspace = tmp_path / "workspace"
    settings = AppSettings(
        work_root=workspace,
        work_dir=workspace,
        cache_dir=workspace / "cache",
    )
    settings.ensure_directories()

    segment = Segment(
        segment_id="seg-1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=0),
    )

    segments_doc = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2025-09-27T00:00:00Z",
        segments=[segment],
    )
    save_segments(segments_doc, settings.segments_file)

    state_doc = StateDocument(
        segments={
            "seg-1": TranslationRecord(
                segment_id="seg-1",
                status=SegmentStatus.PENDING,
            )
        },
        current_provider="test",
        current_model="test-model",
        source_language="en",
        target_language="zh",
    )
    save_state(state_doc, settings.state_file)

    extraction_calls = []
    monkeypatch.setattr("cli.commands.pipeline.run_extraction", lambda **kwargs: extraction_calls.append("extract"))
    translation_calls = []
    monkeypatch.setattr(
        "cli.commands.pipeline.run_translation",
        lambda **kwargs: translation_calls.append("translate"),
    )
    monkeypatch.setattr(
        "cli.commands.export.run_injection",
        lambda **kwargs: ({Path("dummy.xhtml"): b"data"}, {}),
    )
    monkeypatch.setattr("cli.commands.export.export_web", lambda settings, input_epub, **kwargs: input_epub)
    monkeypatch.setattr("cli.core.create_web_archive", lambda path: path.with_suffix(".zip"))

    monkeypatch.setattr("cli.core.load_settings_from_cli", lambda path=None: settings)

    def fake_prepare(ctx, current, epub_path, override):
        return settings

    monkeypatch.setattr("cli.core.prepare_settings_for_epub", fake_prepare)

    runner = CliRunner()
    result = runner.invoke(app, ["pipeline", str(input_epub), "--from", "en", "--to", "zh"])

    assert result.exit_code == 0
    assert not extraction_calls
    assert translation_calls == ["translate"]
    assert "Resuming with existing extraction" in result.output
