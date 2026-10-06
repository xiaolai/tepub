"""translate ends with a summary and an exit code that says whether units
failed, and takes the model, the provider and a dry run from the command line."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import translation.controller as controller
from cli.main import app
from tests.epub_builder import build_epub
from translation.providers import ProviderError


class Model:
    name = "ollama"
    model = "fake"
    uses_markers = True
    follows_instructions = True
    seen: list = []

    def __init__(self, config, fail_on: str | None = None):
        self.config = config
        self.fail_on = fail_on
        Model.seen.append(config)

    def preflight(self) -> None:
        pass

    def translate(self, segment, source_language, target_language):
        if self.fail_on and self.fail_on in segment.source_content:
            raise ProviderError("the model is unhappy")
        return "译文"


@pytest.fixture
def book(tmp_path: Path) -> tuple[Path, Path]:
    epub = build_epub(
        tmp_path / "book.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p><p>Gamma.</p>")]
    )
    root = tmp_path / "w"
    result = CliRunner().invoke(
        app, ["--work-dir", str(root), "extract", str(epub)], prog_name="tepub"
    )
    assert result.exit_code == 0, result.output
    return epub, root


def _translate(book, monkeypatch, *flags, fail_on=None):
    epub, root = book
    Model.seen = []
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config, fail_on))
    result = CliRunner().invoke(
        app, ["--work-dir", str(root), "translate", str(epub), *flags], prog_name="tepub"
    )
    return result, " ".join(result.output.split())


def test_a_clean_run_ends_with_a_summary_and_exit_0(book, monkeypatch) -> None:
    result, output = _translate(book, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "3 of 3 units translated" in output


def test_failed_units_are_reported_and_exit_3(book, monkeypatch) -> None:
    result, output = _translate(book, monkeypatch, fail_on="Beta")
    assert result.exit_code == 3, result.output
    assert "1 failed" in output and "tepub translate" in output


def test_allow_failures_exits_0(book, monkeypatch) -> None:
    result, _ = _translate(book, monkeypatch, "--allow-failures", fail_on="Beta")
    assert result.exit_code == 0


def test_model_and_provider_are_taken_for_this_run(book, monkeypatch) -> None:
    # One unit is left failed, so the last run still has work and creates a
    # provider: with nothing pending, none is created.
    _translate(book, monkeypatch, "--model", "qwen3.5:9b", fail_on="Beta")
    assert Model.seen[-1].name == "ollama" and Model.seen[-1].model == "qwen3.5:9b"
    result, output = _translate(book, monkeypatch, "--provider", "openai")
    assert result.exit_code == 2 and "--model" in output  # another provider needs its model
    _translate(book, monkeypatch, "--provider", "openai", "--model", "gpt-4o")
    assert (Model.seen[-1].name, Model.seen[-1].model, Model.seen[-1].base_url) == (
        "openai",
        "gpt-4o",
        None,
    )


def test_dry_run_reports_and_translates_nothing(book, monkeypatch) -> None:
    result, output = _translate(book, monkeypatch, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "3 units to translate" in output and "ollama" in output
    assert Model.seen == []  # no provider was even created


@pytest.mark.parametrize("flags", [(), ("--dry-run",)])
def test_a_corrupt_state_file_is_reported_not_a_traceback(book, monkeypatch, flags) -> None:
    epub, root = book
    (state_file,) = root.rglob("state.json")
    state_file.write_text("{not json", encoding="utf-8")
    result, output = _translate(book, monkeypatch, *flags)
    assert result.exit_code == 1, result.output
    assert "State file is corrupted: state.json" in output


def test_a_folder_is_not_taken_for_the_book(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["translate", str(tmp_path)], prog_name="tepub")
    assert result.exit_code == 2 and "is a directory" in result.output


@pytest.mark.parametrize("command", [("translate", "--dry-run"), ("translate",), ("export",)])
def test_a_book_not_extracted_leaves_no_workspace_behind(tmp_path: Path, command) -> None:
    """The workspace was created before the check that it was extracted."""
    epub = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>Alpha.</p>")])
    root = tmp_path / "w"
    result = CliRunner().invoke(
        app, ["--work-dir", str(root), command[0], str(epub), *command[1:]], prog_name="tepub"
    )
    assert result.exit_code != 0
    assert not root.exists()
