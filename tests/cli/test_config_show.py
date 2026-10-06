"""config show: the settings in effect and where each came from; keys hidden."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def test_config_show_names_sources_and_hides_keys(tmp_path: Path, monkeypatch) -> None:
    home = Path.home()
    (home / ".tepub").mkdir(parents=True, exist_ok=True)
    (home / ".tepub" / "config.yaml").write_text(
        "primary_provider:\n  name: ollama\n  model: translategemma:12b\ntranslation_workers: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear")
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    root = tmp_path / "w"
    CliRunner().invoke(app, ["--work-dir", str(root), "extract", str(book)], prog_name="tepub")
    (book_config,) = list(root.rglob("config.yaml"))
    book_config.write_text(book_config.read_text(encoding="utf-8") + "\noutput_mode: translated-only\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["--work-dir", str(root), "config", "show", str(book)], prog_name="tepub")
    output = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert "primary_provider.model translategemma:12b global" in output
    assert "translation_workers 2 global" in output
    assert "output_mode translated_only book" in output
    assert "target_language Simplified Chinese default" in output
    assert "OPENAI_API_KEY set" in output and "sk-should-never-appear" not in output
