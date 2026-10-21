"""Opening and closing statements are synthesised once per wording and voice.

They were synthesised again on every assembly, a cover-only rebuild included,
and deleted after each run, so a paid voice was paid again every time.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from audiobook import statements, tts
from audiobook.models import AudioSessionConfig

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class CountingEngine:
    calls = 0

    def synthesize(self, text: str, output_path: Path) -> None:
        CountingEngine.calls += 1
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.5",
             "-ac", "1", "-c:a", "libmp3lame", str(output_path)],
            check=True,
        )


@pytest.fixture
def engine(monkeypatch):
    CountingEngine.calls = 0
    monkeypatch.setattr(tts, "create_tts_engine", lambda **kwargs: CountingEngine())
    return CountingEngine


def _render(
    tmp_path: Path,
    template: str = "Welcome to {book_name}.",
    book_title: str = "Moby-Dick",
    author: str = "Melville",
    **session_changes,
) -> Path | None:
    settings = {"voice": "en-US-JennyNeural", "output_dir": tmp_path, **session_changes}
    session = AudioSessionConfig(**settings)
    return statements._render_statement(
        "opening", template, session, tmp_path, book_title, author
    )


def test_the_same_statement_is_synthesised_once(tmp_path: Path, engine) -> None:
    first = _render(tmp_path)
    second = _render(tmp_path)
    assert engine.calls == 1
    assert first == second and second.exists()


def test_a_different_voice_or_speed_is_synthesised_again(tmp_path: Path, engine) -> None:
    _render(tmp_path)
    _render(tmp_path, voice="en-GB-RyanNeural")
    _render(tmp_path, tts_speed=1.25)
    assert engine.calls == 3


def test_a_different_wording_or_book_is_synthesised_again(tmp_path: Path, engine) -> None:
    """The cache key includes the rendered text; fixed fixtures left that untested."""
    _render(tmp_path)
    _render(tmp_path, template="Welcome to {book_name}, by {author}.")
    _render(tmp_path, book_title="Another Book")

    assert engine.calls == 3
    # The same wording and metadata again is reused, not synthesised twice.
    assert _render(tmp_path, book_title="Another Book") is not None
    assert engine.calls == 3


def test_the_sessions_prosody_reaches_the_engine(tmp_path: Path, monkeypatch) -> None:
    """The body is rendered with rate and volume; the statements ignored them."""
    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return CountingEngine()

    monkeypatch.setattr(tts, "create_tts_engine", factory)

    _render(tmp_path, rate="+10%", volume="-5%")

    assert (captured["rate"], captured["volume"]) == ("+10%", "-5%")


def test_a_failed_statement_leaves_no_intermediate_file(tmp_path: Path, monkeypatch) -> None:
    class Failing:
        def synthesize(self, text: str, output_path: Path) -> None:
            output_path.write_bytes(b"partial")
            raise RuntimeError("provider refused")

    monkeypatch.setattr(tts, "create_tts_engine", lambda **kwargs: Failing())

    with pytest.raises(RuntimeError, match="provider refused"):
        _render(tmp_path)

    statements_dir = tmp_path / "statements"
    assert list(statements_dir.glob("*.mp3")) == []
    assert list(statements_dir.glob("*.aac")) == []
