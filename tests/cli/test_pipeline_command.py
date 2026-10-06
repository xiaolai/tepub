"""pipeline: one command from book to translated book, failures reported."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import translation.controller as controller
from cli.main import app
from tests.cli.test_translate_command import Model
from tests.epub_builder import build_epub


def test_pipeline_exports_what_it_translated_then_exits_3_for_failures(
    tmp_path: Path, monkeypatch
) -> None:
    book = build_epub(
        tmp_path / "books" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p>")]
    )
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config, fail_on="Beta"))
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), str(book), "--to", "zh-CN"], prog_name="tepub"
    )
    assert result.exit_code == 3, result.output
    assert (book.parent / "story.zh-CN.bilingual.epub").exists()
    assert "1 failed" in " ".join(result.output.split())


def test_a_moved_folder_of_book_and_workspace_is_not_extracted_again(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_to_applies_to_a_new_state_even_when_nothing_is_left_to_translate(
    tmp_path: Path, monkeypatch
) -> None:
    """--to was applied after extraction had recorded the configured language,
    and a run with every unit skipped never corrected it."""
    from state.store import load_state

    book = build_epub(
        tmp_path / "story.epub", [("copyright.xhtml", "Copyright", "<p>All rights reserved.</p>")]
    )
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config))
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), str(book), "--to", "fr"], prog_name="tepub"
    )
    assert result.exit_code == 0, result.output
    assert "All segments were skipped" in " ".join(result.output.split())
    (state_file,) = (tmp_path / "w").glob("*/state.json")
    assert load_state(state_file).target_language == "fr"


def test_a_workspace_in_an_older_format_is_updated_not_resumed(tmp_path: Path, monkeypatch) -> None:
    import json

    book = build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p>")])
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config))
    args = ["--work-dir", str(tmp_path / "w"), str(book), "--to", "zh-CN"]
    assert CliRunner().invoke(app, args, prog_name="tepub").exit_code == 0
    (segments_file,) = (tmp_path / "w").glob("*/segments.json")
    data = json.loads(segments_file.read_text(encoding="utf-8"))
    data["format_version"] = 3
    segments_file.write_text(json.dumps(data), encoding="utf-8")

    result = CliRunner().invoke(app, args, prog_name="tepub")
    assert result.exit_code == 0, result.output
    assert "older format" in result.output
    assert json.loads(segments_file.read_text(encoding="utf-8"))["format_version"] > 3
    # The migration is final once the segments are saved; its journal is gone.
    assert not (segments_file.parent / "migration.json").exists()


def test_a_corrupt_state_is_reported_instead_of_re_extracting(tmp_path: Path, monkeypatch) -> None:
    """Re-extraction reads the same artifacts, so it could only fail later,
    after rewriting segments.json."""
    book = build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p>")])
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config))
    args = ["--work-dir", str(tmp_path / "w"), str(book), "--to", "zh-CN"]
    assert CliRunner().invoke(app, args, prog_name="tepub").exit_code == 0
    (state_file,) = (tmp_path / "w").glob("*/state.json")
    state_file.write_text("{broken", encoding="utf-8")
    (segments_file,) = (tmp_path / "w").glob("*/segments.json")
    before = segments_file.read_bytes()

    result = CliRunner().invoke(app, args, prog_name="tepub")
    assert result.exit_code != 0
    assert "Extracting" not in result.output
    assert segments_file.read_bytes() == before


def test_a_directory_is_not_a_book(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["pipeline", str(tmp_path)], prog_name="tepub")
    assert result.exit_code == 2


def test_units_that_cannot_be_inserted_exit_3_unless_allowed(tmp_path: Path, monkeypatch) -> None:
    """Insertion failures were printed and the pipeline exited 0."""
    import json

    book = build_epub(
        tmp_path / "books" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p>")]
    )
    monkeypatch.setattr(controller, "create_provider", lambda config: Model(config))
    args = ["--work-dir", str(tmp_path / "w"), "pipeline", str(book), "--to", "zh-CN"]
    assert CliRunner().invoke(app, args, prog_name="tepub").exit_code == 0
    (segments_file,) = (tmp_path / "w").rglob("segments.json")
    data = json.loads(segments_file.read_text(encoding="utf-8"))
    data["segments"][0]["source_content"] = "Changed."
    segments_file.write_text(json.dumps(data), encoding="utf-8")
    # Its record vouches for the changed text, so the workspace is reused, not
    # extracted again, and the unit no longer matches the book.
    from extraction.pipeline import source_digest
    from state.models import Segment

    state_file = segments_file.with_name("state.json")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    changed = Segment.model_validate(data["segments"][0])
    state["segments"][changed.segment_id]["source_sha256"] = source_digest(changed)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    result = CliRunner().invoke(app, args, prog_name="tepub")
    assert result.exit_code == 3, result.output
    assert "Resuming with the existing workspace" in " ".join(result.output.split())
    result = CliRunner().invoke(app, [*args, "--allow-failures"], prog_name="tepub")
    assert result.exit_code == 0, result.output
