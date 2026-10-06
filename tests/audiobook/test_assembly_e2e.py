"""End-to-end audiobook assembly, checked by reading the finished file.

Only the speech engine is fake; it writes tones. Extraction, chapter grouping,
pauses, concatenation, chapter markers and cover art are the real code, and the
result is measured with ffprobe and mutagen rather than trusted.

Three defects each produced a run that reported success with a wrong book:
chapter starts written in the wrong unit, pauses at the wrong sample rate, and
a concat list cut short by an apostrophe. This test fails on each of them.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from PIL import Image

from audiobook import tts
from audiobook.assembly import assemble_audiobook
from audiobook.concat import decoded_duration
from audiobook.models import (
    AudioSegmentState,
    AudioSegmentStatus,
    AudioSessionConfig,
    AudioStateDocument,
)
from audiobook.state import save_state
from config import AppSettings
from extraction.pipeline import run_extraction
from state.store import load_segments
from tests.epub_builder import build_epub

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg not installed",
)

TONE_SECONDS = 1.0
PAUSE_SECONDS = 1.0


def _tone(path: Path, seconds: float = TONE_SECONDS) -> None:
    codec = ["-c:a", "libmp3lame"] if path.suffix == ".mp3" else ["-c:a", "aac"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=330:sample_rate=24000:duration={seconds}",
         "-ac", "1", *codec, str(path)],
        check=True,
    )


class ToneEngine:
    """Stands in for Edge TTS: every utterance becomes one second of tone."""

    def synthesize(self, text: str, output_path: Path) -> None:
        _tone(Path(output_path))


def _ffprobe_chapters(path: Path) -> list[tuple[float, str]]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [(float(c["start_time"]), c["tags"]["title"]) for c in json.loads(out)["chapters"]]


@pytest.fixture
def assembled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The apostrophe is deliberate: it is what used to truncate the concat list.
    work = tmp_path / "reader's books" / "work"
    epub = build_epub(
        tmp_path / "book.epub",
        [
            ("one.xhtml", "First Chapter", "<p>Alpha paragraph.</p><p>Beta paragraph.</p>"),
            ("two.xhtml", "Second Chapter", "<p>Gamma paragraph.</p><p>Delta paragraph.</p>"),
        ],
        title="Tone Book",
    )
    settings = AppSettings(
        work_dir=work,
        audiobook_opening_statement="Opening of {book_name}.",
        audiobook_closing_statement="The end.",
    )
    run_extraction(settings, epub)
    segments = load_segments(settings.segments_file).segments
    assert len(segments) == 4

    audio_dir = work / "audio"
    audio_dir.mkdir(parents=True)
    states = {}
    for segment in segments:
        path = audio_dir / f"{segment.segment_id}.m4a"
        _tone(path)
        states[segment.segment_id] = AudioSegmentState(
            segment_id=segment.segment_id,
            status=AudioSegmentStatus.COMPLETED,
            audio_path=path,
            duration_seconds=TONE_SECONDS,
        )

    cover = tmp_path / "cover.png"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(cover)
    session = AudioSessionConfig(
        voice="en-US-TestNeural",
        output_dir=work / "audiobook",
        segment_pause_range=(PAUSE_SECONDS, PAUSE_SECONDS),
        cover_path=cover,
    )
    state_path = work / "audio_state.json"
    save_state(AudioStateDocument(session=session, segments=states), state_path)

    monkeypatch.setattr(tts, "create_tts_engine", lambda **kwargs: ToneEngine())
    final = assemble_audiobook(settings, epub, session, state_path, work / "audiobook")
    assert final is not None and final.exists()
    return final


def test_the_book_is_as_long_as_its_parts(assembled: Path) -> None:
    opening_gap = random.Random(0xDEADBEEF).uniform(2.0, 4.0)
    chapter_gap = random.Random(0xA10D10 + 1).uniform(2.0, 4.0)
    closing_gap = random.Random(0xC105ED).uniform(2.0, 4.0)
    chapter = 2 * TONE_SECONDS + PAUSE_SECONDS
    expected = (
        TONE_SECONDS + opening_gap
        + chapter + chapter_gap
        + chapter
        + closing_gap + TONE_SECONDS
    )
    assert decoded_duration(assembled) == pytest.approx(expected, abs=0.25)


def test_chapters_start_where_their_audio_starts(assembled: Path) -> None:
    opening_gap = random.Random(0xDEADBEEF).uniform(2.0, 4.0)
    chapter_gap = random.Random(0xA10D10 + 1).uniform(2.0, 4.0)
    first = TONE_SECONDS + opening_gap
    second = first + 2 * TONE_SECONDS + PAUSE_SECONDS + chapter_gap
    expected = [(first, "First Chapter"), (second, "Second Chapter")]

    by_ffprobe = _ffprobe_chapters(assembled)
    by_mutagen = [(c.start, c.title) for c in MP4(assembled).chapters]
    for reader, found in (("ffprobe", by_ffprobe), ("mutagen", by_mutagen)):
        assert [title for _, title in found] == [title for _, title in expected], reader
        for (start, _), (want, _) in zip(found, expected, strict=True):
            assert start == pytest.approx(want, abs=0.15), (reader, found)


def test_the_cover_is_embedded(assembled: Path) -> None:
    covers = MP4(assembled).tags.get("covr")
    assert covers and len(bytes(covers[0])) > 0
