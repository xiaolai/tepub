"""extract finds the text to translate, and writes nothing else unless asked."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def _extract(tmp_path: Path, *flags: str) -> Path:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "Chapter", "<p>One.</p><p>Two.</p>")])
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "extract", str(book), *flags]
    )
    assert result.exit_code == 0, result.output
    (workspace,) = [p.parent for p in (tmp_path / "w").rglob("segments.json")]
    return workspace


def test_extract_writes_units_and_config_only(tmp_path: Path) -> None:
    """It wrote 286 files on one book: a full unzip and a Markdown export."""
    workspace = _extract(tmp_path)
    assert (workspace / "segments.json").exists() and (workspace / "config.yaml").exists()
    assert not (workspace / "epub_raw").exists()
    assert not (workspace / "markdown").exists()


def test_raw_and_markdown_are_opt_in(tmp_path: Path) -> None:
    workspace = _extract(tmp_path, "--raw", "--markdown")
    assert (workspace / "epub_raw").is_dir()
    assert any((workspace / "markdown").glob("*.md"))


def test_a_rerun_replaces_raw_and_markdown_whole(tmp_path: Path) -> None:
    """Both were written in place, so files the book no longer has stayed."""
    workspace = _extract(tmp_path, "--raw", "--markdown")
    (workspace / "epub_raw" / "stale.txt").write_text("old", encoding="utf-8")
    (workspace / "markdown" / "stale.md").write_text("old", encoding="utf-8")
    _extract(tmp_path, "--raw", "--markdown")
    assert not (workspace / "epub_raw" / "stale.txt").exists()
    assert not (workspace / "markdown" / "stale.md").exists()
    assert any((workspace / "markdown").glob("*.md"))
    assert sorted(p.name for p in workspace.iterdir() if p.name.startswith(".")) == []


def test_a_failed_raw_unzip_fails_the_command_and_keeps_the_last_tree(
    tmp_path: Path, monkeypatch
) -> None:
    """It printed a warning and exited 0, having deleted the previous tree."""
    import importlib

    # cli.commands re-exports the command under the module's name.
    extract_module = importlib.import_module("cli.commands.extract")

    workspace = _extract(tmp_path, "--raw")
    (workspace / "epub_raw" / "keep.txt").write_text("last good", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(extract_module, "extract_epub_structure", fail)
    book = tmp_path / "book.epub"
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "extract", str(book), "--raw"]
    )
    assert result.exit_code == 1
    assert "Could not unzip the book" in result.output and "disk full" in result.output
    assert (workspace / "epub_raw" / "keep.txt").read_text(encoding="utf-8") == "last good"
    assert sorted(p.name for p in workspace.iterdir() if p.name.startswith(".")) == []


def test_a_failed_swap_puts_the_last_tree_back(tmp_path: Path, monkeypatch) -> None:
    """The previous tree was moved aside before the new one was renamed into
    place; when that rename failed, epub_raw was left missing."""
    workspace = _extract(tmp_path, "--raw")
    (workspace / "epub_raw" / "keep.txt").write_text("last good", encoding="utf-8")
    real_rename = Path.rename

    def rename(self, target):
        # The new tree's rename only; the last tree's way back is left open.
        staged = self.name.startswith(".epub_raw.") and not self.name.endswith(".old")
        if Path(target).name == "epub_raw" and staged:
            raise OSError("rename refused")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "extract", str(tmp_path / "book.epub"), "--raw"]
    )
    assert result.exit_code == 1
    assert "rename refused" in result.output
    assert (workspace / "epub_raw" / "keep.txt").read_text(encoding="utf-8") == "last good"
    assert sorted(p.name for p in workspace.iterdir() if p.name.startswith(".")) == []


def test_markdown_converts_each_unit_once_for_both_exports(tmp_path: Path, monkeypatch) -> None:
    """The chapter files and the combined file each loaded the segments, parsed
    the book and converted every unit."""
    import extraction.markdown_export as markdown_export

    converted: list[str] = []
    real = markdown_export._html_to_markdown

    def count(html_content, *args):
        converted.append(html_content)
        return real(html_content, *args)

    monkeypatch.setattr(markdown_export, "_html_to_markdown", count)
    workspace = _extract(tmp_path, "--markdown")
    assert sorted(converted) == ["One.", "Two."]
    assert (workspace / "markdown" / "book.md").read_text(encoding="utf-8").count("One.") == 1


def test_a_failed_markdown_write_is_an_error_not_a_traceback(tmp_path: Path, monkeypatch) -> None:
    import extraction.markdown_export as markdown_export

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(markdown_export, "_write_combined_file", fail)
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "Chapter", "<p>One.</p>")])
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "extract", str(book), "--markdown"]
    )
    assert result.exit_code == 1
    assert "Could not write the Markdown export" in result.output and "disk full" in result.output


def test_a_folder_is_not_taken_for_the_book(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["extract", str(tmp_path)], prog_name="tepub")
    assert result.exit_code == 2 and "is a directory" in result.output
