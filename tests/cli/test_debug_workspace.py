from __future__ import annotations

from click.testing import CliRunner
from importlib import import_module

from config import build_workspace_name
from config import models as config_models


def _load_app():
    return import_module("cli.main").app


def test_debug_workspace_command(monkeypatch, tmp_path) -> None:
    original_root = config_models.DEFAULT_ROOT_DIR
    config_models.DEFAULT_ROOT_DIR = tmp_path / ".tepub"
    monkeypatch.setenv("TEPUB_WORK_ROOT", str(tmp_path / ".tepub"))

    epub_path = tmp_path / "Sample Book.epub"
    epub_path.touch()

    try:
        runner = CliRunner()
        result = runner.invoke(_load_app(), ["debug", "workspace", str(epub_path)])

        # with_book_workspace() derives workspace from EPUB filename (stem)
        # Expected: "Sample Book" (not slugified "sample-55c5211f")
        expected_workspace = epub_path.stem  # "Sample Book"

        assert result.exit_code == 0
        assert expected_workspace in result.output
        assert f"{expected_workspace}/segments.json" in result.output
    finally:
        config_models.DEFAULT_ROOT_DIR = original_root


def test_debug_workspace_respects_cli_override(monkeypatch, tmp_path) -> None:
    epub_path = tmp_path / "Another.epub"
    epub_path.touch()
    override_root = tmp_path / "custom_root"
    monkeypatch.setenv("TEPUB_WORK_ROOT", str(override_root))

    runner = CliRunner()
    result = runner.invoke(_load_app(), ["--work-dir", str(override_root), "debug", "workspace", str(epub_path)])

    # When --work-dir is provided, with_book_workspace() is not used
    # Instead, the workspace is derived from EPUB filename in parent directory
    expected_workspace = epub_path.stem  # "Another"

    assert result.exit_code == 0
    assert expected_workspace in result.output
