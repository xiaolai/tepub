from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from pydub import AudioSegment
from pydub.exceptions import CouldntEncodeError

from audiobook.mp4chapters import _build_chpl_payload, write_chapter_markers


def test_build_chpl_payload_structure():
    payload = _build_chpl_payload([(0.0, "Intro"), (1.25, "Chapter 1")])
    assert payload[:8] == b"\x01\x00\x00\x00\x00\x00\x00\x00"
    count = payload[8]
    assert count == 2
    offset = 9
    starts = []
    for _ in range(count):
        start_raw = int.from_bytes(payload[offset:offset + 8], "big")
        offset += 8
        title_len = payload[offset]
        offset += 1
        title = payload[offset:offset + title_len].decode("utf-8")
        offset += title_len
        starts.append((start_raw, title))
    assert starts[0] == (0, "Intro")
    assert starts[1][0] == 12_500_000  # 1.25 s in 100 ns ticks
    assert starts[1][1] == "Chapter 1"


def test_write_chapter_markers_roundtrip(tmp_path):
    audio = AudioSegment.silent(duration=1000)
    output = Path(tmp_path / "sample.m4a")
    try:
        audio.export(output, format="mp4")
    except CouldntEncodeError:
        pytest.skip("ffmpeg present but could not encode mp4")
    except (FileNotFoundError, OSError):
        # pydub shells out to ffmpeg; when the binary is missing entirely,
        # subprocess raises before pydub can wrap it as CouldntEncodeError.
        pytest.skip("ffmpeg not installed")

    write_chapter_markers(output, [(0, "Intro"), (500, "Middle")])

    mp4 = MP4(output)
    assert mp4.chapters, "Chapters not present after injection"
    assert mp4.chapters[0].title == "Intro"
    assert mp4.chapters[1].title == "Middle"
    assert mp4.chapters[0].start == pytest.approx(0.0, abs=0.01)
    assert mp4.chapters[1].start == pytest.approx(0.5, abs=0.01)


def test_chpl_rejects_more_than_255_chapters():
    """The chpl count field is one byte; 256+ used to raise a bare ValueError."""
    import pytest

    from audiobook.mp4chapters import _build_chpl_payload

    with pytest.raises(ValueError, match="at most 255 chapters"):
        _build_chpl_payload([(float(i), f"Ch {i}") for i in range(256)])


def test_chpl_title_truncation_keeps_valid_utf8():
    """Byte-slicing a multibyte title at 255 could split a codepoint."""
    from audiobook.mp4chapters import _build_chpl_payload

    # One ASCII char shifts the boundary into the middle of a 3-byte character.
    title = "X" + "章" * 200
    body = _build_chpl_payload([(0.0, title)])[8:]

    length = body[9]
    encoded = bytes(body[10 : 10 + length])

    assert length <= 255
    encoded.decode("utf-8")  # must not raise


def _ffprobe_chapter_starts(path: Path) -> list[tuple[float, str]]:
    import json
    import subprocess

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    chapters = json.loads(result.stdout)["chapters"]
    return [(float(c["start_time"]), c["tags"]["title"]) for c in chapters]


@pytest.mark.parametrize("timescale", [1000, 11025, 24000])
def test_chapter_starts_do_not_depend_on_the_movie_timescale(tmp_path, timescale):
    """chpl start times are 100 ns ticks, whatever the movie timescale.

    The writer used to multiply by the file's own timescale, which is only right
    when it is 1000. The audiobook pipeline exports with -movie_timescale 24000,
    so a marker meant for 2 s was read back at 48 s by every player.
    """
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")
    output = tmp_path / "book.m4a"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:a", "aac", "-movie_timescale", str(timescale), str(output),
        ],
        check=True,
    )

    write_chapter_markers(output, [(0, "One"), (2000, "Two"), (4500, "Three")])

    expected = [(0.0, "One"), (2.0, "Two"), (4.5, "Three")]
    by_mutagen = [(c.start, c.title) for c in MP4(output).chapters]
    by_ffprobe = _ffprobe_chapter_starts(output)
    for reader, found in (("mutagen", by_mutagen), ("ffprobe", by_ffprobe)):
        assert [t for _, t in found] == [t for _, t in expected], reader
        for (start, _), (want, _) in zip(found, expected, strict=True):
            assert start == pytest.approx(want, abs=0.01), (reader, found)


def test_wrong_chapter_times_are_refused(tmp_path, monkeypatch):
    """A writer that produces wrong times must fail, not report success."""
    import shutil
    import subprocess

    from audiobook import mp4chapters

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    output = tmp_path / "book.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=6", "-c:a", "aac",
         str(output)],
        check=True,
    )
    real = mp4chapters._build_chpl_payload
    # Reintroduce the old defect: every start 24x too late.
    monkeypatch.setattr(
        mp4chapters,
        "_build_chpl_payload",
        lambda chapters: real([(s * 24, t) for s, t in chapters]),
    )
    with pytest.raises(mp4chapters.ChapterVerificationError, match="chapter 2 should start"):
        mp4chapters.write_chapter_markers(output, [(0, "One"), (2000, "Two")])
