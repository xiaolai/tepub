"""Opening and closing statements are synthesised once per wording and voice.

They were synthesised again on every assembly, a cover-only rebuild included,
and deleted after each run, so a paid voice was paid again every time.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from audiobook import assembly, tts
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


def _render(tmp_path: Path, **session_changes) -> Path | None:
    settings = {"voice": "en-US-JennyNeural", "output_dir": tmp_path, **session_changes}
    session = AudioSessionConfig(**settings)
    return assembly._render_statement("opening", "Welcome to {book_name}.", session, tmp_path, "Moby-Dick", "Melville")


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
