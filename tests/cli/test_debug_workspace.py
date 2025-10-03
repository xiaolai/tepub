from __future__ import annotations

from click.testing import CliRunner
from importlib import import_module

import config
from config import build_workspace_name


def _load_app():
    return import_module("cli.main").app


def test_debug_workspace_command(monkeypatch, tmp_path) -> None:
    original_root = config.DEFAULT_ROOT_DIR
    config.DEFAULT_ROOT_DIR = tmp_path / ".tepub"
    monkeypatch.setenv("TEPUB_WORK_ROOT", str(tmp_path / ".tepub"))

    epub_path = tmp_path / "Sample Book.epub"
    epub_path.touch()

    try:
        runner = CliRunner()
        result = runner.invoke(_load_app(), ["debug", "workspace", str(epub_path)])

        expected_slug = build_workspace_name(epub_path)

        assert result.exit_code == 0
        assert expected_slug in result.output
        assert f"{expected_slug}/segments.json" in result.output
    finally:
        config.DEFAULT_ROOT_DIR = original_root


def test_debug_workspace_respects_cli_override(monkeypatch, tmp_path) -> None:
    epub_path = tmp_path / "Another.epub"
    epub_path.touch()
    override_root = tmp_path / "custom_root"
    monkeypatch.setenv("TEPUB_WORK_ROOT", str(override_root))

    runner = CliRunner()
    result = runner.invoke(_load_app(), ["--work-dir", str(override_root), "debug", "workspace", str(epub_path)])

    expected_workspace = override_root / build_workspace_name(epub_path)

    assert result.exit_code == 0
    assert str(expected_workspace) in result.output
