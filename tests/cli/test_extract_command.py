"""extract finds the text to translate, and writes nothing else unless asked."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def _extract(tmp_path: Path, *flags: str) -> Path:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "Chapter", "<p>One.</p><p>Two.</p>")])
    result = CliRunner().invoke(app, ["--work-dir", str(tmp_path / "w"), "extract", str(book), *flags])
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
