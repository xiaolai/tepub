"""status: where a book stands and the next step; resume is its old name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


class _Output(str):
    """The output with all whitespace removed: long paths wrap anywhere."""

    def __contains__(self, text: object) -> bool:
        return "".join(str(text).split()) in str(self)


def _run(*args: str):
    result = CliRunner().invoke(app, list(args), prog_name="tepub")
    return result, _Output("".join(result.output.split()))


def _book(tmp_path: Path, name: str = "story.epub", title: str = "Fixture") -> tuple[Path, Path]:
    chapter = ("c.xhtml", "Story", "<p>Alpha.</p><p>Beta.</p><p>Gamma.</p>")
    build_epub(tmp_path / name, [chapter], title)
    book, root = Path(name), tmp_path / "w"
    assert _run("--work-dir", str(root), "extract", str(book))[0].exit_code == 0
    return book, root


def _workspace(root: Path) -> Path:
    (workspace,) = [p.parent for p in root.rglob("state.json")]
    return workspace


def _mark(root: Path, statuses: dict[int, str], **languages: str) -> None:
    workspace = _workspace(root)
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    ids = sorted(state["segments"], key=lambda k: int(k.rsplit("-", 1)[1]))
    for index, status in statuses.items():
        state["segments"][ids[index]].update(
            status=status, translation="译" if status == "completed" else None,
            provider_name="ollama", model_name="m",
            error_message="lost a link" if status == "error" else None,
        )
    state["target_language"] = "zh-CN"
    state.update(languages)
    (workspace / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_a_book_not_extracted_says_so(tmp_path: Path) -> None:
    build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>A.</p>")])
    result, output = _run("--work-dir", str(tmp_path / "w"), "status", "story.epub")
    assert result.exit_code == 1
    assert f"Next: tepub --work-dir {tmp_path}/w extract story.epub" in output


def test_status_counts_units_lists_failures_and_suggests_the_next_step(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "error"})
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "1 of 3 translated · 1 failed · 1 pending" in output
    assert "lost a link" in output
    assert f"Next: tepub --work-dir {root} translate story.epub" in output
    assert "with ollama / m (1)" in output


def test_a_finished_book_points_to_export_then_to_done(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert f"Next: tepub --work-dir {root} export story.epub" in output
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "story.zh-CN.bilingual.epub" in output and "Done." in output


def test_status_without_the_book_finds_its_outputs(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    result, output = _run("--work-dir", str(root), "status")
    assert result.exit_code == 0, result.output
    assert "story.zh-CN.bilingual.epub" in output and "Done." in output


def test_outputs_are_matched_literally_and_must_be_files(tmp_path: Path) -> None:
    book, root = _book(tmp_path, "Story [1].epub")
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    (tmp_path / "Story [1].zh-CN.epub").mkdir()
    (tmp_path / "Story [1].de.epub").write_bytes(b"other language")
    (tmp_path / "Story [1].notes.zip").write_bytes(b"unrelated")
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "Output" not in output
    assert f"Next: tepub --work-dir {root} export 'Story [1].epub'" in output
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "Story [1].zh-CN.bilingual.epub" in output and "Done." in output


def test_markup_in_the_title_is_printed_as_text(tmp_path: Path) -> None:
    book, root = _book(tmp_path, title="Odd [/bold] title")
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "Odd [/bold] title" in output


def test_an_unusable_glossary_is_reported_not_raised(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed"})
    glossary = _workspace(root) / "glossary.yaml"
    glossary.write_text("target_language: German\nterms: []\n", encoding="utf-8")
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "Glossary" in output and "is for German" in output


def test_the_glossary_is_counted_without_translation_state(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    workspace = _workspace(root)
    (workspace / "glossary.yaml").write_text(
        "target_language: Simplified Chinese\nterms:\n  - source: Alpha\n    target: 阿尔法\n",
        encoding="utf-8",
    )
    (workspace / "state.json").unlink()
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "Glossary 1 terms" in output


def test_the_next_translate_keeps_the_recorded_languages(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed"}, source_language="en", target_language="de")
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert f"Next: tepub --work-dir {root} translate --from en --to de story.epub" in output


def test_resume_still_works_with_a_warning(tmp_path: Path) -> None:
    book, root = _book(tmp_path)
    result, output = _run("--work-dir", str(root), "resume", str(book))
    assert result.exit_code == 0
    assert "resume is deprecated" in output and "of 3 translated" in output


@pytest.mark.parametrize("command", ["status", "resume", "format"])
def test_another_books_workspace_is_refused(tmp_path: Path, command: str) -> None:
    """--work-dir naming book A's workspace, given book B, reported or changed A."""
    from exceptions import ArtifactMismatchError

    _, root = _book(tmp_path)
    _mark(root, {0: "completed"})
    build_epub(tmp_path / "other.epub", [("c.xhtml", "C", "<p>Else.</p>")])
    state = (_workspace(root) / "state.json").read_bytes()
    result, _ = _run("--work-dir", str(_workspace(root)), command, "other.epub")
    assert isinstance(result.exception, ArtifactMismatchError), result.output
    assert (_workspace(root) / "state.json").read_bytes() == state


@pytest.mark.parametrize("command", ["status", "resume", "format"])
def test_a_book_not_extracted_leaves_no_workspace_behind(tmp_path: Path, command: str) -> None:
    """Resolving the workspace created it, during a query, and failed on
    read-only storage before the guidance."""
    build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>A.</p>")])
    result, _ = _run("--work-dir", str(tmp_path / "w"), command, "story.epub")
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "w").exists()


def test_an_export_written_elsewhere_is_found_and_marked_stale(tmp_path: Path) -> None:
    """status looked beside the book only, and could not tell an old export."""
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    out = tmp_path / "exports"
    assert _run("--work-dir", str(root), "export", str(book), "--out", str(out))[0].exit_code == 0
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert str(out / "story.zh-CN.bilingual.epub") in output and "Done." in output
    assert "older than" not in output

    state_file = _workspace(root) / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    next(iter(state["segments"].values()))["translation"] = "改"
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "older than the translations" in output
    assert f"Next: tepub --work-dir {root} export story.epub" in output


def test_a_book_named_like_an_option_is_suggested_after_a_double_dash(tmp_path: Path) -> None:
    build_epub(tmp_path / "-story.epub", [("c.xhtml", "C", "<p>A.</p><p>B.</p>")])
    root = tmp_path / "w"
    assert _run("--work-dir", str(root), "extract", "--", "-story.epub")[0].exit_code == 0
    _, output = _run("--work-dir", str(root), "status", "--", "-story.epub")
    assert f"Next: tepub --work-dir {root} translate -- -story.epub" in output


def test_a_relative_book_path_is_found_from_another_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded path is as given to extract; from elsewhere the book, and
    so its outputs and the command to copy, were lost."""
    from config import AppSettings
    from extraction.pipeline import run_extraction

    build_epub(tmp_path / "story.epub", [("c.xhtml", "C", "<p>A.</p>")])
    run_extraction(AppSettings(work_dir=tmp_path / "story"), Path("story.epub"))
    (tmp_path / "story.zh-CN.epub").write_bytes(b"an export from before records")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _, output = _run("--work-dir", str(tmp_path / "story"), "status")
    assert "story.zh-CN.epub" in output
    assert f"translate {tmp_path / 'story.epub'}" in output and "<book.epub>" not in output


def test_only_a_recorded_current_file_counts_as_done(tmp_path: Path) -> None:
    """An unrecorded file beside the book, or a recorded path that is now a
    folder, said Done."""
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    (tmp_path / "story.zh-CN.epub").write_bytes(b"not written by export")
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "not recorded by export" in output and "Done." not in output
    assert f"Next: tepub --work-dir {root} export story.epub" in output

    (tmp_path / "story.zh-CN.epub").unlink()
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    exported = tmp_path / "story.zh-CN.bilingual.epub"
    exported.unlink()
    exported.mkdir()
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "Done." not in output and "export story.epub" in output


def test_an_export_in_a_folder_without_permission_is_reported(tmp_path: Path) -> None:
    """exists() raised PermissionError before the per-output handling."""
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    out = tmp_path / "exports"
    assert _run("--work-dir", str(root), "export", str(book), "--out", str(out))[0].exit_code == 0
    out.chmod(0)
    try:
        result, output = _run("--work-dir", str(root), "status", str(book))
    finally:
        out.chmod(0o755)
    assert result.exit_code == 0, result.output
    assert "unreadable" in output and "Done." not in output


def _exported(tmp_path: Path) -> tuple[Path, Path, Path]:
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    assert _run("--work-dir", str(root), "export", str(book))[0].exit_code == 0
    return book, root, tmp_path / "story.zh-CN.bilingual.epub"


@pytest.mark.parametrize("same_size", [False, True])
def test_a_recorded_export_replaced_by_other_contents_is_not_done(
    tmp_path: Path, same_size: bool
) -> None:
    """Only the path, language and translations were checked, so anything
    written over a recorded export counted as it."""
    book, root, exported = _exported(tmp_path)
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "Done." in output and "changed since export" not in output

    size = exported.stat().st_size
    exported.write_bytes(b"x" * size if same_size else b"unrelated")
    _, output = _run("--work-dir", str(root), "status", str(book))
    assert "changed since export" in output and "Done." not in output
    assert f"Next: tepub --work-dir {root} export story.epub" in output


def test_an_export_recorded_without_its_contents_is_not_done(tmp_path: Path) -> None:
    """A record from before export noted the file's size and digest cannot
    vouch for the file, as an unrecorded file cannot."""
    book, root, _ = _exported(tmp_path)
    records = _workspace(root) / "exports.json"
    document = json.loads(records.read_text(encoding="utf-8"))
    for record in document["exports"]:
        assert record.pop("size") > 0 and len(record.pop("sha256")) == 64
    records.write_text(json.dumps(document), encoding="utf-8")
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "recorded without its contents" in output and "Done." not in output


def test_an_output_in_a_symlink_loop_is_reported(tmp_path: Path) -> None:
    """resolve() raised on the loop and stopped status."""
    book, root = _book(tmp_path)
    _mark(root, {0: "completed", 1: "completed", 2: "completed"})
    loop = tmp_path / "story.zh-CN.epub"
    loop.symlink_to(loop)
    result, output = _run("--work-dir", str(root), "status", str(book))
    assert result.exit_code == 0, result.output
    assert "story.zh-CN.epub" in output and "unreadable" in output and "Done." not in output
