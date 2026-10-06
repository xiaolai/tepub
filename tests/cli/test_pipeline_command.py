"""pipeline: one command from book to translated book, failures reported."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import translation.controller as controller
from cli.main import app
from tests.cli.test_translate_command import Model
from tests.epub_builder import build_epub


def test_pipeline_exports_what_it_translated_then_exits_3_for_failures(tmp_path: Path, monkeypatch) -> None:
    book = build_epub(tmp_path / "books" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p>")])
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config, fail_on="Beta"))
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), str(book), "--to", "zh-CN"], prog_name="tepub"
    )
    assert result.exit_code == 3, result.output
    assert (book.parent / "story.zh-CN.bilingual.epub").exists()
    assert "1 failed" in " ".join(result.output.split())


def test_a_moved_folder_of_book_and_workspace_is_not_extracted_again(tmp_path: Path, monkeypatch) -> None:
    """The workspace records the book's old path; comparing paths re-extracted a
    book whose folder had moved, workspace and all. It is matched by content."""
    import os

    book = build_epub(tmp_path / "a" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p>")])
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config))
    monkeypatch.chdir(tmp_path)
    first = CliRunner().invoke(app, [str(book)], prog_name="tepub")
    assert first.exit_code == 0, first.output
    os.rename(tmp_path / "a", tmp_path / "b")
    second = CliRunner().invoke(app, [str(tmp_path / "b" / "story.epub")], prog_name="tepub")
    assert second.exit_code == 0, second.output
    assert "Resuming with the existing workspace" in " ".join(second.output.split())
