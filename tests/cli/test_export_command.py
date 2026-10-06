"""export writes what its options say, next to the book unless told otherwise.

It wrote the bilingual EPUB, the translated EPUB and a web version whatever
--epub or --output-mode said, and wrote them inside the workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


@pytest.fixture
def translated(tmp_path: Path) -> tuple[Path, Path]:
    book = build_epub(
        tmp_path / "books" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p>")]
    )
    root = tmp_path / "w"
    result = CliRunner().invoke(
        app, ["--work-dir", str(root), "extract", str(book)], prog_name="tepub"
    )
    assert result.exit_code == 0, result.output
    (workspace,) = [p.parent for p in root.rglob("segments.json")]
    segments = json.loads((workspace / "segments.json").read_text(encoding="utf-8"))["segments"]
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    for s in segments:
        state["segments"][s["segment_id"]].update(
            translation="译文", status="completed", provider_name="x"
        )
    state["target_language"] = "zh-CN"
    (workspace / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return book, root


def _export(translated, *flags: str):
    book, root = translated
    result = CliRunner().invoke(
        app, ["--work-dir", str(root), "export", str(book), *flags], prog_name="tepub"
    )
    return result, " ".join(result.output.split())


def _beside(book: Path) -> list[str]:
    return sorted(p.name for p in book.parent.iterdir() if p.name != book.name)


def test_default_writes_the_configured_edition_next_to_the_book(translated) -> None:
    result, _ = _export(translated)
    assert result.exit_code == 0, result.output
    assert _beside(translated[0]) == ["story.zh-CN.bilingual.epub"]


def test_mode_both_and_translated(translated) -> None:
    _export(translated, "--mode", "both")
    assert _beside(translated[0]) == ["story.zh-CN.bilingual.epub", "story.zh-CN.epub"]


@pytest.mark.parametrize("spelling", ["translated", "translated-only", "translated_only"])
def test_every_spelling_of_translated_only(translated, spelling) -> None:
    result, _ = _export(translated, "--mode", spelling)
    assert result.exit_code == 0, result.output
    assert _beside(translated[0]) == ["story.zh-CN.epub"]


def test_web_format_and_out_dir(translated, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result, _ = _export(translated, "--format", "web", "--format", "epub", "--out", str(out))
    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in out.iterdir())
    assert "story.zh-CN.bilingual.epub" in names and "story.zh-CN.web.zip" in names
    assert _beside(translated[0]) == []


def test_old_flags_still_work_with_a_warning(translated) -> None:
    result, output = _export(translated, "--epub", "--output-mode", "translated-only")
    assert result.exit_code == 0, result.output
    assert "--epub is deprecated" in output and "--output-mode is deprecated" in output
    assert _beside(translated[0]) == ["story.zh-CN.epub"]


def test_old_output_epub_path_is_honoured(translated, tmp_path: Path) -> None:
    target = tmp_path / "custom.epub"
    result, output = _export(translated, "--output-epub", str(target))
    assert result.exit_code == 0, result.output
    assert target.exists() and "--output-epub is deprecated" in output


def _workspace(translated) -> Path:
    (workspace,) = [p.parent for p in translated[1].rglob("state.json")]
    return workspace


def test_a_target_language_holding_a_path_is_refused(translated) -> None:
    """The code names the outputs and the web folder export_web deletes."""
    state_file = _workspace(translated) / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["target_language"] = "x/../../escape"
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    result, output = _export(translated, "--format", "web")
    assert result.exit_code == 1
    assert "cannot be part of a file name" in output
    assert not list(translated[1].parent.rglob("escape*"))


def test_output_epub_naming_the_book_is_refused(translated) -> None:
    book = translated[0]
    before = book.read_bytes()
    result, output = _export(translated, "--output-epub", str(book))
    assert result.exit_code == 2
    assert "overwrite the book itself" in output
    assert book.read_bytes() == before


def test_out_inside_the_web_folder_is_refused(translated) -> None:
    """The web export replaces its folder, deleting the EPUBs just written there,
    and the archive would hold itself."""
    out = _workspace(translated) / "story.zh-CN.web" / "out"
    result, output = _export(translated, "--format", "web", "--format", "epub", "--out", str(out))
    assert result.exit_code == 2
    assert "inside the web export's folder" in output
    assert not out.exists()


def test_both_modes_on_the_web_write_both_editions(translated, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result, _ = _export(translated, "--mode", "both", "--format", "web", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        "story.zh-CN.bilingual.web.zip",
        "story.zh-CN.web.zip",
    ]


def test_a_failed_archive_keeps_the_previous_one(translated, tmp_path: Path, monkeypatch) -> None:
    import cli.commands.export as export_module

    out = tmp_path / "out"
    out.mkdir()
    (out / "story.zh-CN.web.zip").write_bytes(b"previous")

    def fail(base_name, *args, **kwargs):
        Path(f"{base_name}.zip").write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(export_module.shutil, "make_archive", fail)
    result, output = _export(translated, "--format", "web", "--out", str(out))
    assert result.exit_code == 1
    assert "Could not write" in output and "disk full" in output
    assert sorted(p.name for p in out.iterdir()) == ["story.zh-CN.web.zip"]
    assert (out / "story.zh-CN.web.zip").read_bytes() == b"previous"


def test_an_out_folder_that_cannot_be_made_is_an_error(translated, tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    result, output = _export(translated, "--out", str(blocker / "sub"))
    assert result.exit_code == 1
    assert "Could not create" in output


def test_a_folder_is_not_taken_for_the_book(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["export", str(tmp_path)], prog_name="tepub")
    assert result.exit_code == 2 and "is a directory" in result.output


def _break_a_unit(translated) -> None:
    """The book's text no longer matches one extracted unit."""
    workspace = _workspace(translated)
    data = json.loads((workspace / "segments.json").read_text(encoding="utf-8"))
    data["segments"][0]["source_content"] = "Changed."
    (workspace / "segments.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("flags", [(), ("--format", "web")])
def test_units_that_cannot_be_inserted_exit_3(translated, flags) -> None:
    """Insertion failures were printed and the export exited 0."""
    _break_a_unit(translated)
    result, output = _export(translated, *flags)
    assert result.exit_code == 3, result.output
    assert "1 translated units are missing" in output


def test_export_records_what_it_wrote(translated, tmp_path: Path) -> None:
    result, _ = _export(translated, "--out", str(tmp_path / "out"), "--mode", "both")
    assert result.exit_code == 0, result.output
    records = json.loads((_workspace(translated) / "exports.json").read_text(encoding="utf-8"))
    assert sorted(r["path"] for r in records["exports"]) == [
        str((tmp_path / "out" / name).resolve())
        for name in ("story.zh-CN.bilingual.epub", "story.zh-CN.epub")
    ]


def test_output_epub_naming_the_book_by_another_path_is_refused(translated, tmp_path: Path) -> None:
    """Paths were compared as strings, so a second name for the same file, such
    as macOS's /System/Volumes/Data/... or a hard link, passed the check."""
    import os

    book = translated[0]
    other_name = tmp_path / "same-book.epub"
    os.link(book, other_name)
    before = book.read_bytes()
    result, output = _export(translated, "--output-epub", str(other_name))
    assert result.exit_code == 2
    assert "overwrite the book itself" in output
    assert book.read_bytes() == before


def test_each_archive_is_staged_under_its_own_name(translated, tmp_path: Path, monkeypatch) -> None:
    """One fixed staging name let a concurrent export publish this run's
    half-written archive as its own."""
    import cli.commands.export as export_module

    real = export_module.shutil.make_archive
    staged: list[str] = []

    def record(base_name, *args, **kwargs):
        staged.append(base_name)
        return real(base_name, *args, **kwargs)

    monkeypatch.setattr(export_module.shutil, "make_archive", record)
    out = tmp_path / "out"
    for _ in range(2):
        result, _ = _export(translated, "--format", "web", "--out", str(out))
        assert result.exit_code == 0, result.output
    assert len(staged) == 2 and staged[0] != staged[1]
    assert sorted(p.name for p in out.iterdir()) == ["story.zh-CN.web.zip"]


def test_a_failed_cleanup_still_reports_the_failed_archive(
    translated, tmp_path: Path, monkeypatch
) -> None:
    import cli.commands.export as export_module

    def fail(base_name, *args, **kwargs):
        raise OSError("disk full")

    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith(".story"):
            raise PermissionError("read-only")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(export_module.shutil, "make_archive", fail)
    monkeypatch.setattr(Path, "unlink", unlink)
    result, output = _export(translated, "--format", "web", "--out", str(tmp_path / "out"))
    assert result.exit_code == 1
    assert "Could not write" in output and "disk full" in output
    assert not isinstance(result.exception, PermissionError)


def test_export_waits_for_no_other_run_on_the_workspace(translated) -> None:
    """Export read the segments and the state unlocked, so an extract between
    the two reads gave it the units of one extraction and the translations of
    another."""
    from state.writer import exclusive_run

    with exclusive_run(_workspace(translated) / "state.json"):
        result, output = _export(translated)
    assert result.exit_code == 1
    assert "another tepub process" in output
    assert _beside(translated[0]) == []


def test_export_formats_chinese_under_its_own_lock(translated) -> None:
    """The typography pass took the workspace lock inside every edition; it
    runs once, under the lock export holds."""
    state_file = _workspace(translated) / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    for record in state["segments"].values():
        record["translation"] = "第3章"
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    result, output = _export(translated, "--mode", "both", "--format", "web", "--format", "epub")
    assert result.exit_code == 0, result.output
    assert output.count("Formatting translated text") == 1
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert {r["translation"] for r in state["segments"].values()} == {"第 3 章"}


def test_epub_and_web_of_one_mode_share_one_injection(
    translated, tmp_path: Path, monkeypatch
) -> None:
    """Each loaded the segments and the state, parsed the book and inserted
    every translation on its own."""
    import cli.commands.export as export_module

    modes: list[str] = []
    real = export_module.apply_translations

    def count(settings, input_epub, *, mode):
        modes.append(mode)
        return real(settings, input_epub, mode=mode)

    import injection.engine
    import webbuilder.exporter

    for module in (export_module, injection.engine, webbuilder.exporter):
        monkeypatch.setattr(module, "apply_translations", count)
    out = tmp_path / "out"
    result, _ = _export(
        translated, "--mode", "both", "--format", "epub", "--format", "web", "--out", str(out)
    )
    assert result.exit_code == 0, result.output
    assert sorted(modes) == ["bilingual", "translated_only"]
    assert len(list(out.iterdir())) == 4
