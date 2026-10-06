"""The command line's front door: version, typos, the book shortcut, global
options anywhere, and help in workflow order."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def _run(*args: str):
    return CliRunner().invoke(app, list(args), prog_name="tepub")


def test_version() -> None:
    result = _run("--version")
    assert result.exit_code == 0
    assert importlib.metadata.version("tepub") in result.output


def test_a_mistyped_command_is_named_with_a_suggestion(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    result = _run("transalte", str(book))
    assert result.exit_code == 2
    assert "No such command 'transalte'" in result.output
    assert result.output.count("Did you mean 'translate'") == 1


def test_an_existing_epub_alone_runs_the_pipeline(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    result = _run(str(book), "--help")
    assert result.exit_code == 0 and "Usage: tepub pipeline" in result.output


def test_a_file_that_is_not_an_epub_is_not_taken_for_one(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    result = _run(str(notes))
    assert result.exit_code == 2 and "No such command" in result.output


def test_global_options_work_after_the_command(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    root = tmp_path / "root"
    after = _run("debug", "workspace", str(book), "--work-dir", str(root))
    before = _run("--work-dir", str(root), "debug", "workspace", str(book))
    assert after.exit_code == 0, after.output
    assert after.output == before.output and str(root) in " ".join(after.output.split())


def test_help_lists_the_workflow_in_order() -> None:
    result = _run("--help")
    commands = [line.split()[0] for line in result.output.split("Commands:")[1].splitlines() if line.strip()]
    assert commands[:4] == ["extract", "translate", "export", "pipeline"]
    assert "tepub extract book.epub" in result.output  # an example of the path


def test_no_command_reuses_a_global_option_name() -> None:
    """Global options found after a command are moved before it, which is only
    unambiguous while no command defines an option of the same name."""
    import click

    globals_ = {opt for param in app.params for opt in (*param.opts, *param.secondary_opts)}

    def walk(command: click.Command, path: str):
        for param in command.params:
            if isinstance(param, click.Option):
                clash = set(param.opts) & globals_
                assert not clash, f"{path} defines {clash}"
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, f"{path} {name}")

    for name, command in app.commands.items():
        walk(command, name)
