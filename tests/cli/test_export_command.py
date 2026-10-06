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
    book = build_epub(tmp_path / "books" / "story.epub", [("c.xhtml", "C", "<p>Alpha.</p><p>Beta.</p>")])
    root = tmp_path / "w"
    result = CliRunner().invoke(app, ["--work-dir", str(root), "extract", str(book)], prog_name="tepub")
    assert result.exit_code == 0, result.output
    (workspace,) = [p.parent for p in root.rglob("segments.json")]
    segments = json.loads((workspace / "segments.json").read_text(encoding="utf-8"))["segments"]
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    for s in segments:
        state["segments"][s["segment_id"]].update(translation="译文", status="completed", provider_name="x")
    state["target_language"] = "zh-CN"
    (workspace / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return book, root


def _export(translated, *flags: str):
    book, root = translated
    result = CliRunner().invoke(app, ["--work-dir", str(root), "export", str(book), *flags], prog_name="tepub")
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
