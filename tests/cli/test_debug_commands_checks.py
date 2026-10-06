"""Debug commands: argument checks, and purge-refusals reporting what it did."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from config import AppSettings
from extraction.pipeline import run_extraction
from state.models import SegmentStatus
from state.store import load_state, save_state
from tests.epub_builder import build_epub

REFUSAL = "I'm sorry, I can't help with that."


def _translated_workspace(tmp_path: Path, translation: str) -> tuple[Path, AppSettings]:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    state = load_state(settings.state_file)
    for sid, record in state.segments.items():
        state.segments[sid] = record.model_copy(
            update={"translation": translation, "status": SegmentStatus.COMPLETED}
        )
    save_state(state, settings.state_file)
    return book, settings


def test_purge_refusals_reports_what_it_reset_not_what_it_first_read(
    tmp_path: Path, monkeypatch
) -> None:
    """The count came from the unlocked first read; a translate run that
    replaced a refusal in between was still reported as reset."""
    import state.writer

    book, settings = _translated_workspace(tmp_path, REFUSAL)
    real_load = state.writer.load_state

    def load_after_a_retranslation(path):
        doc = real_load(path)
        first = next(iter(doc.segments))
        doc.segments[first] = doc.segments[first].model_copy(update={"translation": "一"})
        return doc

    monkeypatch.setattr(state.writer, "load_state", load_after_a_retranslation)
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "debug", "purge-refusals", str(book)]
    )
    assert result.exit_code == 0, result.output
    assert "Reset 1 segments" in result.output


def test_purge_refusals_reports_a_busy_workspace(tmp_path: Path) -> None:
    from state.writer import exclusive_run

    book, settings = _translated_workspace(tmp_path, REFUSAL)
    with exclusive_run(settings.state_file):
        result = CliRunner().invoke(
            app, ["--work-dir", str(tmp_path / "w"), "debug", "purge-refusals", str(book)]
        )
    assert result.exit_code == 1
    assert "Could not update" in result.output


def test_show_skip_list_takes_the_book(tmp_path: Path) -> None:
    book, _ = _translated_workspace(tmp_path, "译")
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "debug", "show-skip-list", str(book)]
    )
    assert result.exit_code == 0, result.output


def test_epub_arguments_refuse_a_directory(tmp_path: Path) -> None:
    for command in ("preview-skip-candidates", "workspace"):
        result = CliRunner().invoke(app, ["debug", command, str(tmp_path)])
        assert result.exit_code == 2, (command, result.output)


def test_analyze_skips_needs_a_library_and_sane_counts(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["debug", "analyze-skips"]).exit_code == 2
    for option in (["--limit", "0"], ["--top-n", "-1"]):
        result = runner.invoke(app, ["debug", "analyze-skips", "--library", str(tmp_path), *option])
        assert result.exit_code == 2, (option, result.output)


def test_purge_refusals_reports_a_state_file_it_cannot_read(tmp_path: Path) -> None:
    """The first read's OSError escaped as a traceback."""
    book, settings = _translated_workspace(tmp_path, REFUSAL)
    settings.state_file.chmod(0)
    try:
        result = CliRunner().invoke(
            app, ["--work-dir", str(tmp_path / "w"), "debug", "purge-refusals", str(book)]
        )
    finally:
        settings.state_file.chmod(0o644)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "Could not read" in result.output
