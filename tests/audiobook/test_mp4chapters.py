from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from pydub import AudioSegment
from pydub.exceptions import CouldntEncodeError

from audiobook.mp4chapters import _build_chpl_payload, write_chapter_markers


def test_build_chpl_payload_structure():
    payload = _build_chpl_payload([(0.0, "Intro"), (1.25, "Chapter 1")], 1000)
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
    assert starts[1][0] == int(1.25 * 1000 * 10000)
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
        _build_chpl_payload([(float(i), f"Ch {i}") for i in range(256)], 1000)


def test_chpl_title_truncation_keeps_valid_utf8():
    """Byte-slicing a multibyte title at 255 could split a codepoint."""
    from audiobook.mp4chapters import _build_chpl_payload

    # One ASCII char shifts the boundary into the middle of a 3-byte character.
    title = "X" + "章" * 200
    body = _build_chpl_payload([(0.0, title)], 1000)[8:]

    length = body[9]
    encoded = bytes(body[10 : 10 + length])

    assert length <= 255
    encoded.decode("utf-8")  # must not raise
