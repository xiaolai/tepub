"""An audiobook run with no terminal does not pick a voice by itself."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import sys

from cli.main import app
from tests.epub_builder import build_epub

VOICES = [
    {"ShortName": "en-AU-NatashaNeural", "Locale": "en-AU"},
    {"ShortName": "en-US-GuyNeural", "Locale": "en-US"},
]


def test_no_voice_and_no_terminal_stops_with_how_to_choose(tmp_path: Path, monkeypatch) -> None:
    # The package re-exports the group under the module's name; take the module.
    module = sys.modules["cli.commands.audiobook"]
    monkeypatch.setattr(module, "list_voices_for_provider", lambda *a, **k: VOICES)
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    extracted = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "extract", str(book)], prog_name="tepub"
    )
    assert extracted.exit_code == 0, extracted.output
    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "audiobook", "generate", str(book)], prog_name="tepub"
    )
    output = " ".join(result.output.split())
    assert result.exit_code == 2, result.output
    assert "--voice" in output and "en-US-GuyNeural" in output
    assert "Using voice" not in output
