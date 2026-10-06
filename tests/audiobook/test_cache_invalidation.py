"""Characterization tests for audiobook cache invalidation.

These cover behaviour that was previously unprotected: audiobook/assembly.py and
audiobook/state.py sat at ~11% and ~13% coverage, so the fixes for stale chapter
reuse and stale audio reuse had no regression guard.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from audiobook.chapter_plan import chapter_is_current
from audiobook.models import (
    AudioSegmentState,
    AudioSegmentStatus,
    AudioStateDocument,
)
from audiobook.state import apply_text_digests, ensure_state, load_state, save_state
from state.models import ExtractMode, Segment, SegmentMetadata


def _segment(seg_id: str) -> Segment:
    return Segment(
        segment_id=seg_id,
        file_path=Path("Text/ch1.xhtml"),
        xpath=f"/html/body/p[{seg_id[-1]}]",
        extract_mode=ExtractMode.TEXT,
        source_content="text",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


def _audio_state(tmp_path: Path, *seg_ids: str) -> AudioStateDocument:
    segments = {}
    for seg_id in seg_ids:
        audio = tmp_path / f"{seg_id}.m4a"
        audio.write_bytes(b"audio")
        segments[seg_id] = AudioSegmentState(
            segment_id=seg_id, status=AudioSegmentStatus.COMPLETED, audio_path=audio
        )
    return AudioStateDocument(
        session={"voice": "v", "output_dir": tmp_path}, segments=segments
    )


def _chapter(tmp_path: Path, *seg_ids: str) -> Path:
    """A cached chapter built from seg_ids, newer than its sources."""
    chapter = tmp_path / "001-ch.m4a"
    chapter.write_bytes(b"chapter")
    chapter.with_suffix(chapter.suffix + ".segments").write_text(
        "\n".join(seg_ids), encoding="utf-8"
    )
    future = time.time() + 10
    os.utime(chapter, (future, future))
    return chapter


class TestChapterCache:
    def test_unchanged_chapter_is_reused(self, tmp_path):
        state = _audio_state(tmp_path, "s1", "s2")
        chapter = _chapter(tmp_path, "s1", "s2")

        assert chapter_is_current(chapter, [_segment("s1"), _segment("s2")], state) is True

    def test_dropped_segment_invalidates_chapter(self, tmp_path):
        """A+B cached, run now includes only A.

        Every remaining source is older than the chapter, so a timestamp-only
        check reused a chapter that still contained B.
        """
        state = _audio_state(tmp_path, "s1", "s2")
        chapter = _chapter(tmp_path, "s1", "s2")

        assert chapter_is_current(chapter, [_segment("s1")], state) is False

    def test_added_segment_invalidates_chapter(self, tmp_path):
        state = _audio_state(tmp_path, "s1", "s2")
        chapter = _chapter(tmp_path, "s1")

        assert chapter_is_current(chapter, [_segment("s1"), _segment("s2")], state) is False

    def test_reordered_segments_invalidate_chapter(self, tmp_path):
        state = _audio_state(tmp_path, "s1", "s2")
        chapter = _chapter(tmp_path, "s1", "s2")

        assert chapter_is_current(chapter, [_segment("s2"), _segment("s1")], state) is False

    def test_newer_segment_audio_invalidates_chapter(self, tmp_path):
        """Re-synthesising a segment must rebuild the chapter containing it."""
        state = _audio_state(tmp_path, "s1")
        chapter = _chapter(tmp_path, "s1")
        resynthesised = time.time() + 3600
        os.utime(tmp_path / "s1.m4a", (resynthesised, resynthesised))

        assert chapter_is_current(chapter, [_segment("s1")], state) is False

    def test_chapter_without_manifest_is_not_reused(self, tmp_path):
        """Written by an older version: composition unknown, so rebuild."""
        state = _audio_state(tmp_path, "s1")
        chapter = _chapter(tmp_path, "s1")
        chapter.with_suffix(chapter.suffix + ".segments").unlink()

        assert chapter_is_current(chapter, [_segment("s1")], state) is False

    def test_missing_chapter_is_not_reused(self, tmp_path):
        state = _audio_state(tmp_path, "s1")

        assert chapter_is_current(tmp_path / "nope.m4a", [_segment("s1")], state) is False


class TestAudioSettingInvalidation:
    """Changing a setting that affects the rendered audio must discard it."""

    @staticmethod
    def _completed(tmp_path: Path):
        path = tmp_path / "audio_state.json"
        state = ensure_state(path, tmp_path, "en-US-GuyNeural", tts_speed=1.0, language="en")
        state.segments["s1"] = AudioSegmentState(
            segment_id="s1", status=AudioSegmentStatus.COMPLETED, audio_path=tmp_path / "a.m4a"
        )
        save_state(state, path)
        return path

    @pytest.mark.parametrize(
        "changed",
        [
            {"voice": "en-US-JennyNeural"},
            {"tts_speed": 1.5},
            {"language": "fr"},
            {"tts_provider": "openai"},
            {"tts_model": "tts-1-hd"},
            {"rate": "+10%"},
            {"volume": "+5%"},
        ],
    )
    def test_audio_affecting_change_invalidates(self, tmp_path, changed):
        path = self._completed(tmp_path)
        kwargs = {"voice": "en-US-GuyNeural", "tts_speed": 1.0, "language": "en"}
        kwargs.update(changed)

        ensure_state(path, tmp_path, **kwargs)

        record = load_state(path).segments["s1"]
        assert record.status == AudioSegmentStatus.PENDING
        assert record.audio_path is None

    def test_no_op_rerun_preserves_completed_audio(self, tmp_path):
        path = self._completed(tmp_path)

        ensure_state(path, tmp_path, "en-US-GuyNeural", tts_speed=1.0, language="en")

        assert load_state(path).segments["s1"].status == AudioSegmentStatus.COMPLETED

    def test_unspecified_speed_keeps_stored_speed(self, tmp_path):
        path = tmp_path / "audio_state.json"
        ensure_state(path, tmp_path, "en-US-GuyNeural", tts_speed=1.5, language="en")
        state = load_state(path)
        state.segments["s1"] = AudioSegmentState(
            segment_id="s1", status=AudioSegmentStatus.COMPLETED, audio_path=tmp_path / "a.m4a"
        )
        save_state(state, path)

        ensure_state(path, tmp_path, "en-US-GuyNeural", tts_speed=None, language="en")

        reloaded = load_state(path)
        assert reloaded.session.tts_speed == 1.5
        assert reloaded.segments["s1"].status == AudioSegmentStatus.COMPLETED

    def test_new_session_records_prosody_and_default_speed(self, tmp_path):
        path = tmp_path / "audio_state.json"

        ensure_state(path, tmp_path, "en-US-GuyNeural", rate="+10%", volume="-5%")

        session = load_state(path).session
        assert (session.rate, session.volume, session.tts_speed) == ("+10%", "-5%", 1.0)

    def test_cover_change_alone_does_not_discard_audio(self, tmp_path):
        """The cover is container artwork; it does not affect rendered audio."""
        path = self._completed(tmp_path)

        ensure_state(
            path, tmp_path, "en-US-GuyNeural", tts_speed=1.0, language="en",
            cover_path=tmp_path / "cover.jpg",
        )

        assert load_state(path).segments["s1"].status == AudioSegmentStatus.COMPLETED


class TestTextDigests:
    """Completed audio is reused only while the text it speaks is unchanged."""

    @staticmethod
    def _state(tmp_path: Path, digest: str | None) -> Path:
        path = tmp_path / "audio_state.json"
        state = _audio_state(tmp_path, "s1")
        state.segments["s1"].text_sha256 = digest
        save_state(state, path)
        return path

    def test_matching_digest_keeps_audio(self, tmp_path):
        path = self._state(tmp_path, "aaa")

        assert apply_text_digests(path, {"s1": "aaa"}) == []
        assert load_state(path).segments["s1"].status == AudioSegmentStatus.COMPLETED

    def test_changed_text_resets_audio(self, tmp_path):
        path = self._state(tmp_path, "aaa")

        assert apply_text_digests(path, {"s1": "bbb"}) == ["s1"]
        record = load_state(path).segments["s1"]
        assert record.status == AudioSegmentStatus.PENDING
        assert record.audio_path is None

    def test_state_without_digest_loads_and_is_backfilled(self, tmp_path):
        path = self._state(tmp_path, None)
        payload = path.read_text(encoding="utf-8").replace('"text_sha256": null,', "")
        path.write_text(payload, encoding="utf-8")
        assert "text_sha256" not in path.read_text(encoding="utf-8")

        assert apply_text_digests(path, {"s1": "aaa"}) == []
        record = load_state(path).segments["s1"]
        assert record.status == AudioSegmentStatus.COMPLETED
        assert record.text_sha256 == "aaa"
