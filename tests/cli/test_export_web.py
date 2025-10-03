from pathlib import Path

from click.testing import CliRunner

from cli.main import app


def test_export_web_invokes_builder(monkeypatch, tmp_path):
    import json
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")

    # Create mock workspace with required state files
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()

    # Create valid segments file
    segments_data = {
        "epub_path": str(input_epub),
        "generated_at": "2024-01-01T00:00:00",
        "segments": []
    }
    (work_dir / "segments.json").write_text(json.dumps(segments_data))

    # Create valid state file
    state_data = {
        "provider_name": "test",
        "model_name": "test-model",
        "source_language": "en",
        "target_language": "zh",
        "segments": {}
    }
    (work_dir / "state.json").write_text(json.dumps(state_data))

    called = {"epub": False, "web": False}

    def fake_prepare_settings(ctx, s, i, override):
        from config import AppSettings
        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
            state_file=work_dir / "state.json",
        )
        ctx.obj["settings"] = settings
        return settings

    monkeypatch.setattr("cli.core.prepare_settings_for_epub", fake_prepare_settings)

    def fake_run_injection(  # type: ignore[override]
        settings,
        input_epub,
        output_epub,
        mode="bilingual",
    ):
        called["epub"] = True
        return ({Path("dummy.xhtml"): b"data"}, {})

    def fake_export_web(settings, input_epub):
        called["web"] = True
        return tmp_path / "web"

    monkeypatch.setattr("cli.commands.export.run_injection", fake_run_injection)
    monkeypatch.setattr("cli.core.create_web_archive", lambda path: path.with_suffix(".zip"))
    monkeypatch.setattr("cli.commands.export.export_web", lambda settings, input_epub, **kwargs: fake_export_web(settings, input_epub))

    runner = CliRunner()
    result = runner.invoke(app, ["export", str(input_epub), "--web"])

    assert result.exit_code == 0
    assert called["web"]
    assert not called["epub"]
    assert "Web version exported" in result.output
    assert "Wrote translated EPUB" not in result.output
