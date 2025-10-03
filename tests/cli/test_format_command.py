from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from config import AppSettings
from state.models import SegmentStatus, StateDocument, TranslationRecord
from state.store import save_state


def test_format_command_polishes_translations(tmp_path, monkeypatch):
    settings = AppSettings().model_copy(update={"work_dir": tmp_path, "target_language": "Simplified Chinese"})
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
