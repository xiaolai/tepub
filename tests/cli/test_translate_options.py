from pathlib import Path

from click.testing import CliRunner

from cli.main import app


def test_translate_uses_language_flags(monkeypatch, tmp_path):
    import json
    epub_path = tmp_path / "book.epub"
    epub_path.write_text("stub", encoding="utf-8")

    # Create workspace with required state files
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()

    # Create valid segments file
    segments_data = {
        "epub_path": str(epub_path),
        "generated_at": "2024-01-01T00:00:00",
        "segments": []
    }
    (work_dir / "segments.json").write_text(json.dumps(segments_data))

    called = {}

    def fake_prepare(ctx, settings, input_epub, override):
        from config import AppSettings
        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
        )
        ctx.obj["settings"] = settings
        return settings

    def fake_run_translation(settings, input_epub, source_language, target_language):
        called["source_language"] = source_language
        called["target_language"] = target_language

    monkeypatch.setattr("cli.core.prepare_settings_for_epub", fake_prepare)
    monkeypatch.setattr("translation.controller.run_translation", fake_run_translation)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "translate",
            "--from",
            "en",
            "--to",
            "zh-CN",
            str(epub_path),
        ],
    )

    assert result.exit_code == 0
    assert called["source_language"] == "en"
    assert called["target_language"] == "zh-CN"
