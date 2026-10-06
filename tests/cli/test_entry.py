"""The command line's front door: version, typos, the book shortcut, global
options anywhere, and help in workflow order."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
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
    listing = result.output.split("Commands:")[1].splitlines()
    commands = [line.split()[0] for line in listing if line.strip()]
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


def test_global_flags_written_together_work_after_the_command() -> None:
    result = _run("translate", "-vq", "--help")
    assert result.exit_code == 0, result.output


def test_a_command_option_value_is_not_taken_for_a_global_option() -> None:
    group = app
    args = ["translate", "book.epub", "--model", "-q", "--work-dir", "w"]
    assert group._hoist_global_options(args, 0) == [
        "--work-dir", "w", "translate", "book.epub", "--model", "-q",
    ]


def test_nothing_after_a_double_dash_is_moved() -> None:
    args = ["--", "translate", "--quiet", "book.epub"]
    assert app._hoist_global_options(args, 1) == args


def test_config_must_be_a_file(tmp_path: Path) -> None:
    result = _run("--config", str(tmp_path), "translate", "--help")
    assert result.exit_code == 2 and "is a directory" in result.output


def test_a_malformed_config_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("target_language: [unclosed\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output
    assert isinstance(result.exception, SystemExit)


def test_an_invalid_setting_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("translation_workers: many\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output


def test_a_null_work_dir_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """Path(None) raised a TypeError that escaped as a traceback."""
    config = tmp_path / "config.yaml"
    config.write_text("work_dir: null\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output


def test_a_config_that_is_a_lone_value_is_an_error(tmp_path: Path) -> None:
    """`false` was read as no settings, and the run went ahead without them."""
    config = tmp_path / "config.yaml"
    config.write_text("false\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output
    assert "not a bool" in result.output


def test_quiet_silences_settings_warnings(tmp_path: Path, caplog) -> None:
    """Log records went to the logging handler's own console, which -q missed."""
    config = tmp_path / "config.yaml"
    config.write_text("no_such_setting: 1\n", encoding="utf-8")
    try:
        _run("--config", str(config), "translate", "--help")
        loud = caplog.text
        caplog.clear()
        _run("-q", "--config", str(config), "translate", "--help")
        quiet = caplog.text
    finally:
        _run("--help")  # leaves logging enabled for the tests after this one
    assert "Ignoring settings" in loud
    assert "Ignoring settings" not in quiet


def test_an_abort_that_is_not_ctrl_c_exits_1(monkeypatch, capsys) -> None:
    """A declined confirmation claimed "Interrupted. Progress is saved." and 130."""
    import click
    import pytest

    import cli.main as main

    def decline(**kwargs):
        raise click.exceptions.Abort()

    monkeypatch.setattr(main.app, "main", decline)
    with pytest.raises(SystemExit) as exit_info:
        main.run()
    assert exit_info.value.code == 1
    assert "Interrupted" not in capsys.readouterr().out


def test_ctrl_c_inside_click_still_exits_130(monkeypatch) -> None:
    import click
    import pytest

    import cli.main as main

    def interrupted(**kwargs):
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt as exc:
            raise click.exceptions.Abort() from exc

    def exit_now(code: int):
        raise SystemExit(code)

    monkeypatch.setattr(main.app, "main", interrupted)
    monkeypatch.setattr(main.os, "_exit", exit_now)
    with pytest.raises(SystemExit) as exit_info:
        main.run()
    assert exit_info.value.code == 130


@pytest.mark.parametrize("text", ["null\n", "~\n", "!!null null\n"])
def test_a_config_that_is_an_explicit_null_is_an_error(tmp_path: Path, text: str) -> None:
    """An explicit null root became {} and the run went ahead without settings."""
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output


def test_a_global_config_that_is_an_explicit_null_is_an_error() -> None:
    folder = Path.home() / ".tepub"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text("~\n", encoding="utf-8")
    result = _run("translate", "--help")
    assert result.exit_code == 1, result.output
    assert "The configuration is not valid" in result.output


def test_a_config_that_holds_only_comments_sets_nothing(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("# nothing set yet\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 0, result.output


def test_a_numeric_setting_name_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """`1: x` raised TypeError when sorted beside the text keys."""
    config = tmp_path / "config.yaml"
    config.write_text("1: x\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "setting names must be text" in result.output


@pytest.mark.parametrize("key", ["work_dir", "work_root"])
def test_a_path_naming_no_user_is_an_error_not_a_traceback(tmp_path: Path, key: str) -> None:
    """expanduser raised RuntimeError for `~name` with no such user."""
    config = tmp_path / "config.yaml"
    config.write_text(f"{key}: ~nonexistentuser-tepub/x\n", encoding="utf-8")
    result = _run("--config", str(config), "translate", "--help")
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "The configuration is not valid" in result.output


def test_a_work_dir_option_naming_no_user_is_an_error_not_a_traceback() -> None:
    result = _run("--work-dir", "~nonexistentuser-tepub/x", "translate", "--help")
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "The configuration is not valid" in result.output


def test_a_work_root_variable_naming_no_user_is_an_error(monkeypatch) -> None:
    monkeypatch.setenv("TEPUB_WORK_ROOT", "~nonexistentuser-tepub/x")
    result = _run("translate", "--help")
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "text",
    ["work_dir: ~nonexistentuser-tepub/x\n", "target_language: [unclosed\n"],
)
def test_a_bad_book_config_is_an_error_not_a_traceback(
    tmp_path: Path, monkeypatch, capsys, text: str
) -> None:
    """A book's own config.yaml was applied unguarded: a bad value or bad YAML
    there escaped the entry point as a traceback."""
    import cli.main as main

    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    assert _run("extract", str(book)).exit_code == 0
    (tmp_path / "book" / "config.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["tepub", "translate", str(book), "--dry-run"])

    with pytest.raises(SystemExit) as exit_info:
        main.run()

    out = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "config.yaml" in out.out + out.err
    assert "Traceback" not in out.out + out.err

