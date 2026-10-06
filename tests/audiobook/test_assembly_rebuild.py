"""Assembly when a book is rebuilt: what is reused, what is replaced, what survives.

Segment audio is real (ffmpeg tones) so the real concatenation runs; individual
steps are wrapped or made to fail to observe what assembly does around them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from PIL import Image

from audiobook import assembly
from audiobook.assembly import assemble_audiobook
from audiobook.concat import container_duration
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


def _tone(path: Path, frequency: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={frequency}:sample_rate=24000:duration=0.5",
         "-ac", "1", "-c:a", "aac", str(path)],
        check=True,
    )


class Book:
    def __init__(self, tmp_path: Path, chapters, title: str = "Rebuild Book") -> None:
        self.work = tmp_path / "work"
        self.epub = build_epub(tmp_path / "book.epub", chapters, title=title)
        self.settings = AppSettings(work_dir=self.work)
        run_extraction(self.settings, self.epub)
        self.segments = load_segments(self.settings.segments_file).segments
        self.audio: dict[str, Path] = {}
        audio_dir = self.work / "audio"
        audio_dir.mkdir(parents=True)
        states = {}
        for index, segment in enumerate(self.segments):
            path = audio_dir / f"{segment.segment_id}.m4a"
            _tone(path, 200 + 50 * index)
            self.audio[segment.segment_id] = path
            states[segment.segment_id] = AudioSegmentState(
                segment_id=segment.segment_id,
                status=AudioSegmentStatus.COMPLETED,
                audio_path=path,
            )
        self.output_root = self.work / "audiobook"
        self.state_path = self.work / "audio_state.json"
        self.session = AudioSessionConfig(
            voice="en-US-TestNeural",
            output_dir=self.output_root,
            segment_pause_range=(0.5, 0.5),
        )
        save_state(AudioStateDocument(session=self.session, segments=states), self.state_path)

    def assemble(self, included: set[str] | None = None) -> Path | None:
        return assemble_audiobook(
            self.settings, self.epub, self.session, self.state_path, self.output_root, included
        )

    def chapters(self) -> list[Path]:
        return sorted((self.output_root / "chapters").glob("*.m4a"))

    def touch_audio(self, segment_id: str) -> None:
        later = time.time() + 3600
        os.utime(self.audio[segment_id], (later, later))


@pytest.fixture
def concat_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[Path], Path]]:
    calls: list[tuple[list[Path], Path]] = []
    real = assembly.concat_audio

    def recording(inputs: list[Path], output: Path) -> Path:
        calls.append((list(inputs), output))
        return real(inputs, output)

    monkeypatch.setattr(assembly, "concat_audio", recording)
    return calls


TWO_CHAPTERS = [
    ("one.xhtml", "First Chapter", "<p>Alpha.</p><p>Beta.</p>"),
    ("two.xhtml", "Second Chapter", "<p>Gamma.</p><p>Delta.</p>"),
]


def test_chapter_spanning_documents_keeps_reading_order(tmp_path, concat_calls) -> None:
    # two.xhtml has no TOC entry, so it belongs to the chapter of one.xhtml.
    book = Book(tmp_path, [
        ("one.xhtml", "Only Chapter", "<p>Alpha.</p><p>Beta.</p>"),
        ("two.xhtml", "", "<p>Gamma.</p><p>Delta.</p>"),
    ])

    book.assemble()

    chapter_inputs = concat_calls[0][0]
    spoken = [path for path in chapter_inputs if path in book.audio.values()]
    assert spoken == [book.audio[segment.segment_id] for segment in book.segments]


def test_chapter_title_cannot_steer_scratch_cleanup(tmp_path) -> None:
    book = Book(tmp_path, [("one.xhtml", "x/../../keep", "<p>Alpha.</p><p>Beta.</p>")])
    keep = book.output_root / "keep"
    keep.mkdir(parents=True)
    (keep / "precious").write_text("do not delete")

    book.assemble()

    assert (keep / "precious").exists()
    assert not [p for p in (book.output_root / "chapters").iterdir() if p.is_dir()]


def test_overlong_title_still_makes_a_book(tmp_path) -> None:
    book = Book(tmp_path, [("one.xhtml", "Word " * 80, "<p>Alpha.</p>")], title="Title " * 80)

    final = book.assemble()

    assert final is not None and final.exists()
    assert all(len(path.name.encode()) <= 255 for path in book.output_root.rglob("*"))


def test_empty_selection_synthesises_no_statements(tmp_path, monkeypatch) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)

    def no_tts(*args, **kwargs):
        raise AssertionError("statement synthesised for a book with no chapters")

    monkeypatch.setattr(assembly, "_render_statement", no_tts)

    assert book.assemble(included=set()) is None


def test_only_the_stale_chapter_is_rebuilt(tmp_path, concat_calls) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    book.assemble()
    first, second = book.chapters()
    concat_calls.clear()

    book.touch_audio(book.segments[3].segment_id)
    book.assemble()

    outputs = [output for _, output in concat_calls]
    assert len(outputs) == 2  # the second chapter and the final book
    assert outputs[0].name == f"{second.stem}.partial.m4a"


def test_failed_chapter_rebuild_leaves_no_manifest_vouching_for_it(tmp_path, monkeypatch) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    book.assemble()
    first = book.chapters()[0]
    before = first.read_bytes()
    book.touch_audio(book.segments[0].segment_id)

    def broken(inputs, output):
        output.write_bytes(b"truncated")
        raise assembly.ConcatError("disk full")

    monkeypatch.setattr(assembly, "concat_audio", broken)
    with pytest.raises(assembly.ConcatError):
        book.assemble()

    assert first.read_bytes() == before
    assert not first.with_suffix(".m4a.segments").exists()
    assert not list((book.output_root / "chapters").glob("*.partial.m4a"))


def test_failed_final_rebuild_keeps_the_previous_book(tmp_path, monkeypatch) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    final = book.assemble()
    assert final is not None
    before = final.read_bytes()
    book.touch_audio(book.segments[0].segment_id)

    def broken(path, *args, **kwargs):
        raise OSError("tagging failed")

    monkeypatch.setattr(assembly, "_tag_audiobook", broken)
    with pytest.raises(OSError):
        book.assemble()

    assert final.read_bytes() == before
    assert not list(book.output_root.glob("*.partial.m4a"))


def test_chapter_that_gains_a_successor_gets_its_gap(tmp_path) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    first_only = {segment.segment_id for segment in book.segments[:2]}
    book.assemble(included=first_only)
    alone = container_duration(book.chapters()[0])

    book.assemble()

    # Built last, it had no gap; with a chapter after it, it needs one.
    assert container_duration(book.chapters()[0]) > alone + 1.5


def test_changed_pause_range_rebuilds_chapters(tmp_path) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    book.assemble()
    short = container_duration(book.chapters()[0])

    book.session = book.session.model_copy(update={"segment_pause_range": (1.5, 1.5)})
    book.assemble()

    assert container_duration(book.chapters()[0]) == pytest.approx(short + 1.0, abs=0.15)


def test_new_cover_reaches_cached_chapters(tmp_path) -> None:
    book = Book(tmp_path, TWO_CHAPTERS)
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(red)
    Image.new("RGB", (64, 64), (30, 30, 200)).save(blue)
    book.session = book.session.model_copy(update={"cover_path": red})
    book.assemble()
    red_art = bytes(MP4(book.chapters()[0]).tags["covr"][0])

    book.session = book.session.model_copy(update={"cover_path": blue})
    final = book.assemble()

    chapter_art = bytes(MP4(book.chapters()[0]).tags["covr"][0])
    assert chapter_art != red_art
    assert chapter_art == bytes(MP4(final).tags["covr"][0])


def test_long_titles_sharing_a_prefix_get_distinct_filenames() -> None:
    shared = "A History of Everything " * 10
    first = assembly._file_slug(shared + "Volume One")
    second = assembly._file_slug(shared + "Volume Two")

    assert first != second
    assert len(first) <= assembly._MAX_SLUG_LENGTH
    assert len(second) <= assembly._MAX_SLUG_LENGTH
    assert assembly._file_slug("Short Title") == assembly._slugify("Short Title")
