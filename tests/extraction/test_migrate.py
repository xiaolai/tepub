"""Carrying a workspace from the old segmentation to the new one.

Codex's refute review: old extraction produced (A, A, B) for
<blockquote><p>A</p></blockquote><p>B</p>, the new one produces (A, B); mapping
by order would give B the second A's translation. Matching is by file and text.
"""

from __future__ import annotations

import json
from pathlib import Path

from extraction.migrate import import_legacy_workspace, legacy_mapping
from state.models import (
    ExtractMode,
    Segment,
    SegmentMetadata,
    SegmentStatus,
    StateDocument,
    TranslationRecord,
)
from state.store import load_state, save_state

FILE = Path("text/ch1.xhtml")


def _segment(segment_id: str, order: int, text: str, mode=ExtractMode.TEXT, file=FILE) -> Segment:
    return Segment(
        segment_id=segment_id,
        file_path=file,
        xpath="/x",
        extract_mode=mode,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=order),
    )


OLD = [_segment("old-bq", 1, "A"), _segment("old-p", 2, "A"), _segment("old-b", 3, "B")]
NEW = [_segment("new-1", 1, "A"), _segment("new-2", 2, "B")]


def test_matching_is_by_text_not_by_position() -> None:
    mapping = legacy_mapping(OLD, NEW)
    assert mapping["old-b"] == "new-2"
    assert mapping["old-bq"] == "new-1"
    assert "old-p" not in mapping


def test_html_and_text_segments_match_on_their_text() -> None:
    old = [_segment("o", 1, "<li>One</li><li>Two</li>", ExtractMode.HTML)]
    new = [_segment("n", 1, "<li>One</li>\n<li>Two</li>", ExtractMode.HTML)]
    assert legacy_mapping(old, new) == {"o": "n"}


def test_equal_text_in_different_files_does_not_match() -> None:
    old = [_segment("o", 1, "Same.", file=Path("a/ch.xhtml"))]
    new = [_segment("n", 1, "Same.", file=Path("b/ch.xhtml"))]
    assert legacy_mapping(old, new) == {}


def test_import_moves_translations_and_audio(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    save_state(
        StateDocument(
            segments={
                "old-bq": TranslationRecord(segment_id="old-bq", translation="甲", status=SegmentStatus.COMPLETED),
                "old-p": TranslationRecord(segment_id="old-p", translation="甲二", status=SegmentStatus.COMPLETED),
                "old-b": TranslationRecord(segment_id="old-b", translation="乙", status=SegmentStatus.COMPLETED),
            }
        ),
        state_file,
    )
    audio_dir = tmp_path / "audiobook@edgetts"
    audio_dir.mkdir()
    (audio_dir / "audio_state.json").write_text(
        json.dumps({"session": {}, "segments": {"old-b": {"segment_id": "old-b", "audio_path": "b.m4a"}}}),
        encoding="utf-8",
    )

    report = import_legacy_workspace(tmp_path, state_file, OLD, NEW)

    state = load_state(state_file)
    assert state.segments["new-2"].translation == "乙"  # B keeps B's translation
    assert state.segments["new-1"].translation == "甲"
    assert set(state.segments) == {"new-1", "new-2"}
    assert report.finished_not_carried == 1
    audio = json.loads((audio_dir / "audio_state.json").read_text(encoding="utf-8"))
    assert audio["segments"] == {"new-2": {"segment_id": "new-2", "audio_path": "b.m4a"}}
    assert len(report.backups) == 2 and all(p.exists() for p in report.backups)
