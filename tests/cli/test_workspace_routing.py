"""Commands without a book find the same workspace that translate wrote to.

With --work-dir X, translate and extract use a per-book folder inside X, while
resume, format and debug show-pending read X itself: they reported "no state"
for a book that was half translated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.core import resolve_bookless_workspace
from config import AppSettings
from exceptions import WorkspaceNotFoundError


def _workspace(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "segments.json").write_text("{}", encoding="utf-8")
    return path


def test_a_folder_that_is_a_workspace_is_used_as_is(tmp_path: Path) -> None:
    _workspace(tmp_path / "w")
    settings = resolve_bookless_workspace(AppSettings(work_dir=tmp_path / "w"))
    assert settings.work_dir == tmp_path / "w"


def test_a_single_book_workspace_inside_is_found(tmp_path: Path) -> None:
    book = _workspace(tmp_path / "root" / "moby-dick-1a2b3c")
    settings = resolve_bookless_workspace(AppSettings(work_dir=tmp_path / "root"))
    assert settings.work_dir == book
    assert settings.state_file == book / "state.json"


def test_several_book_workspaces_are_listed_not_guessed(tmp_path: Path) -> None:
    _workspace(tmp_path / "root" / "book-a")
    _workspace(tmp_path / "root" / "book-b")
    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        resolve_bookless_workspace(AppSettings(work_dir=tmp_path / "root"))
    assert "book-a" in str(excinfo.value) and "book-b" in str(excinfo.value)


def test_no_workspace_at_all_leaves_settings_unchanged(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    settings = resolve_bookless_workspace(AppSettings(work_dir=tmp_path / "empty"))
    assert settings.work_dir == tmp_path / "empty"


def test_resume_finds_what_extract_wrote_under_the_same_work_dir(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from cli.main import app
    from tests.epub_builder import build_epub

    book = build_epub(tmp_path / "book.epub", [("ch1.xhtml", "One", "<p>One.</p><p>Two.</p>")])
    root = tmp_path / "work"
    runner = CliRunner()

    extracted = runner.invoke(app, ["--work-dir", str(root), "extract", str(book)])
    assert extracted.exit_code == 0, extracted.output

    resumed = runner.invoke(app, ["--work-dir", str(root), "resume"])
    assert resumed.exit_code == 0, resumed.output
    assert "No translation state" not in resumed.output
    assert "Remaining" in resumed.output
