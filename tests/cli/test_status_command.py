"""status: where a book stands and the next step; resume is its old name."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def _run(*args: str):
    result = CliRunner().invoke(app, list(args), prog_name="tepub")
    return result, " ".join(result.output.split())


def _book(tmp_path: Path) -> tuple[Path, Path]:
    book = build_epub(tmp_path / "story.epub", [("c.xhtml", "Story", "<p>Alpha.</p><p>Beta.</p><p>Gamma.</p>")])
    root = tmp_path / "w"
    assert _run("--work-dir", str(root), "extract", str(book))[0].exit_code == 0
    return book, root


def _mark(root: Path, statuses: dict[int, str]) -> None:
    (workspace,) = [p.parent for p in root.rglob("state.json")]
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    ids = sorted(state["segments"], key=lambda k: int(k.rsplit("-", 1)[1]))
    for index, status in statuses.items():
        state["segments"][ids[index]].update(
            status=status, translation="译" if status == "completed" else None,
            provider_name="ollama", model_name="m", error_message="lost a link" if status == "error" else None,
        )
    state["target_language"] = "zh-CN"
    (workspace / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_a_book_not_extracted_says_so(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>A.</p>")])
    result, output = _run("--work-dir", str(tmp_path / "w"), "status", str(book))
    assert result.exit_code == 1 and "Next: tepub extract story.epub" in output


def test_status_counts_units_lists_failures_and_suggests_the_next_step(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "error"})
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "1 of 3 translated · 1 failed · 1 pending" in output
    assert "lost a link" in output and "Next: tepub translate story.epub" in output
    assert "with ollama / m (1)" in output


def test_a_finished_book_points_to_export_then_to_done(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    assert "Next: tepub export story.epub" in _run("--work-dir", str(root), "status", str(book))[1]
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "story.zh-CN.bilingual.epub" in output and "Done." in output


def test_resume_still_works_with_a_warning(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    result, output = _run("--work-dir", str(root), "resume", str(book))
    assert result.exit_code == 0 and "resume is deprecated" in output and "of 3 translated" in output
