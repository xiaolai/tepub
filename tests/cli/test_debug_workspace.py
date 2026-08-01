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

    # --work-dir must place the workspace under the given root. This previously
    # asserted the workspace appeared next to the EPUB instead, which is what the
    # flag being ignored looks like: the override was recorded only on `settings`,
    # and derive_book_workspace() ignores settings.work_dir entirely, returning
    # `<epub parent>/<epub stem>`.
    assert result.exit_code == 0
    assert str(override_root) in result.output
    # Workspace dir is named from the EPUB, slugified with a path digest.
    assert epub_path.stem.lower() in result.output
