"""Console text built from paths, book names and error messages is printed literally.

Rich reads brackets as markup: "[draft]" vanished from a path, and "[/blue]"
raised MarkupError in the middle of an error report.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import cli.main as cli_main
from cli.errors import handle_state_errors
from cli.main import app
from exceptions import ArtifactMismatchError, TepubError, WorkspaceNotFoundError
from tests.epub_builder import build_epub

NASTY = "[draft] x.epub [/blue] [bold]"


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _squash(text: str) -> str:
    return "".join(text.split())


def test_the_entry_point_prints_a_tepub_error_literally(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error = ArtifactMismatchError(Path("/books/[draft] x.epub"), Path("/old/[/blue] y.epub"))

    def fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(cli_main.app, "main", fail)
    with pytest.raises(SystemExit) as stop:
        cli_main.run()
    captured = capsys.readouterr()
    output = _squash(captured.out + captured.err)
    assert stop.value.code == 1
    assert "Traceback" not in output and "MarkupError" not in output
    assert _squash("/books/[draft] x.epub") in output
    assert _squash("/old/[/blue] y.epub") in output


def test_the_entry_point_prints_a_plain_message_literally(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise TepubError(NASTY)

    monkeypatch.setattr(cli_main.app, "main", fail)
    with pytest.raises(SystemExit) as stop:
        cli_main.run()
    captured = capsys.readouterr()
    assert stop.value.code == 1
    assert _squash(NASTY) in _squash(captured.out + captured.err)


def test_the_state_error_decorator_prints_literally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @handle_state_errors
    def command() -> None:
        raise WorkspaceNotFoundError(Path("/w/[draft] x"), "extract it [/blue] first")

    with pytest.raises(click.exceptions.Exit) as stop:
        command()
    captured = capsys.readouterr()
    assert stop.value.exit_code == 1
    assert _squash("[draft] x") in _squash(captured.out + captured.err)
    assert _squash("[/blue]") in _squash(captured.out + captured.err)


def test_a_command_reporting_a_bracketed_book_exits_cleanly(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "[draft] x.epub", [("c.xhtml", "C", "<p>One.</p>")])
    result = CliRunner().invoke(app, ["status", str(book)], prog_name="tepub")
    assert "MarkupError" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_successful_command_prints_a_bracketed_path_literally(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "[draft] x.epub", [("c.xhtml", "C", "<p>One.</p>")])
    result = CliRunner().invoke(app, ["extract", str(book)], prog_name="tepub")
    assert result.exit_code == 0, result.output
    # The default workspace is named after the book; wrapped lines lose spaces.
    assert _squash("[draft] x/segments.json") in _squash(result.output)
