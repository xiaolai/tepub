
from click.testing import CliRunner

from cli.main import app
from config import AppSettings
from state.models import SegmentStatus, StateDocument, TranslationRecord
from state.store import save_state


def test_format_command_polishes_translations(tmp_path, monkeypatch):
    settings = AppSettings().model_copy(
        update={"work_dir": tmp_path, "target_language": "Simplified Chinese"}
    )
    settings.ensure_directories()
    state_path = settings.state_file
    state = StateDocument(
        segments={
            "seg-1": TranslationRecord(
                segment_id="seg-1",
                translation="这是2024年的报告",
                status=SegmentStatus.COMPLETED,
            )
        },
        source_language="auto",
        target_language="Simplified Chinese",
    )
    save_state(state, state_path)

    runner = CliRunner()
    result = runner.invoke(app, ["--work-dir", str(tmp_path), "format"])

    assert result.exit_code == 0
    assert "Formatted translations saved" in result.output

    from state.store import load_state

    new_state = load_state(state_path)
    assert new_state.segments["seg-1"].translation == "这是 2024 年的报告"


def _state(tmp_path, target_language: str) -> StateDocument:
    return StateDocument(
        segments={
            "seg-1": TranslationRecord(
                segment_id="seg-1", translation="这是2024年的报告", status=SegmentStatus.COMPLETED
            )
        },
        source_language="auto",
        target_language=target_language,
    )


def test_format_decides_on_the_state_it_rewrites(tmp_path, monkeypatch):
    import cli.commands.format as format_module
    from state.store import load_state

    state_path = tmp_path / "state.json"
    save_state(_state(tmp_path, "Simplified Chinese"), state_path)
    real_update = format_module.update_state_atomic

    def replaced_meanwhile(path, updater):
        # Another run rewrote the book into English after format started.
        save_state(_state(tmp_path, "English"), path)
        return real_update(path, updater)

    monkeypatch.setattr(format_module, "update_state_atomic", replaced_meanwhile)
    result = CliRunner().invoke(app, ["--work-dir", str(tmp_path), "format"])

    assert result.exit_code == 0, result.output
    assert "not Chinese" in result.output
    assert load_state(state_path).segments["seg-1"].translation == "这是2024年的报告"


def test_format_reports_an_unreadable_state_file(tmp_path):
    state_path = tmp_path / "state.json"
    save_state(_state(tmp_path, "Simplified Chinese"), state_path)
    state_path.chmod(0)
    try:
        result = CliRunner().invoke(app, ["--work-dir", str(tmp_path), "format"])
    finally:
        state_path.chmod(0o644)

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "could not be formatted" in result.output


def test_format_without_state_creates_no_workspace(tmp_path):
    missing = tmp_path / "nowhere"
    result = CliRunner().invoke(app, ["--work-dir", str(missing), "format"])

    assert result.exit_code == 1 and "State file not found" in result.output
    assert not missing.exists()


def test_format_reports_a_state_file_it_cannot_reach(tmp_path):
    """The existence check raised PermissionError before any diagnostic."""
    from pathlib import Path

    (tmp_path / "segments.json").write_text("{}", encoding="utf-8")
    hidden = tmp_path / "private"
    hidden.mkdir()
    (tmp_path / "config.yaml").write_text("state_file: private/state.json\n", encoding="utf-8")
    hidden.chmod(0)
    try:
        result = CliRunner().invoke(app, ["--work-dir", str(tmp_path), "format"])
    finally:
        hidden.chmod(0o755)

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "could not be read" in result.output
    assert Path(tmp_path / "private").is_dir()
