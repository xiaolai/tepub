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


def test_workspaces_from_0_4_0_are_carried_over_too() -> None:
    """Format 2 sources still held bare <a/> elements; matching ignores markup."""
    from extraction.migrate import SEGMENTS_FORMAT

    assert SEGMENTS_FORMAT >= 3
    old = [_segment("o", 1, "Before<a/> after <a href='#n'>1</a>.", ExtractMode.HTML)]
    new = [_segment("n", 1, 'Before after <a href="#n">1</a>.', ExtractMode.HTML)]
    assert legacy_mapping(old, new) == {"o": "n"}


def _carry(tmp_path: Path, old: Segment, new: Segment, translation: str):
    state_file = tmp_path / "state.json"
    record = TranslationRecord(
        segment_id=old.segment_id, translation=translation, status=SegmentStatus.COMPLETED
    )
    save_state(StateDocument(segments={old.segment_id: record}), state_file)
    report = import_legacy_workspace(tmp_path, state_file, [old], [new])
    carried = load_state(state_file).segments.get(new.segment_id)
    return (carried.translation if carried else None), report


def test_a_unit_that_became_text_loses_the_tags_of_its_translation(tmp_path: Path) -> None:
    """0.4.0 sent a paragraph with a bare <a> as HTML; it is text now, and a
    tag left in its translation would be shown to the reader as text."""
    old = _segment("o", 1, "Before <a>word</a> after.", ExtractMode.HTML)
    new = _segment("n", 1, "Before word after.")
    assert _carry(tmp_path, old, new, "之前<a>词</a>之后。")[0] == "之前词之后。"


def test_a_unit_that_became_html_keeps_a_translation_that_meets_the_contract(tmp_path: Path) -> None:
    old = _segment("o", 1, "A & B.")
    new = _segment("n", 1, "A <em>&amp;</em> B.", ExtractMode.HTML)
    assert _carry(tmp_path, old, new, "甲 & 乙。")[0] == "甲 &amp; 乙。"


def test_a_unit_that_became_html_with_a_link_is_translated_again(tmp_path: Path) -> None:
    """A plain translation cannot carry the link the unit now must keep."""
    old = _segment("o", 1, "See note 1.")
    new = _segment("n", 1, 'See note <a href="#n1">1</a>.', ExtractMode.HTML)
    translation, report = _carry(tmp_path, old, new, "见注释 1。")
    assert translation is None
    assert report.finished_not_carried == 1


def test_bare_links_left_in_an_html_translation_are_unwrapped(tmp_path: Path) -> None:
    old = _segment("o", 1, 'Before<a/> <a>x</a> and <a href="#n">1</a>.', ExtractMode.HTML)
    new = _segment("n", 1, 'Before x and <a href="#n">1</a>.', ExtractMode.HTML)
    translation = _carry(tmp_path, old, new, '之前<a/> <a>x</a> 和 <a href="#n">1</a>。')[0]
    assert translation == '之前 x 和 <a href="#n">1</a>。'


def test_a_whole_list_translation_is_split_onto_its_items(tmp_path: Path) -> None:
    """Format 4 splits long lists into items; a list translated whole is
    carried item by item, and keeps who translated it."""
    old = _segment("old-list", 1, '<li><a href="#r1">1.</a> First note.</li><li>Second note.</li>', ExtractMode.HTML)
    new = [
        _segment("new-1", 1, '<a href="#r1">1.</a> First note.', ExtractMode.HTML),
        _segment("new-2", 2, "Second note."),
    ]
    state_file = tmp_path / "state.json"
    record = TranslationRecord(
        segment_id="old-list",
        translation='<li><a href="#r1">1.</a> 第一条注释。</li><li>第二条<b>注释</b>。</li>',
        status=SegmentStatus.COMPLETED,
        provider_name="ollama",
        model_name="translategemma:12b",
    )
    save_state(StateDocument(segments={"old-list": record}), state_file)

    report = import_legacy_workspace(tmp_path, state_file, [old], new)

    state = load_state(state_file)
    assert state.segments["new-1"].translation == '<a href="#r1">1.</a> 第一条注释。'
    assert state.segments["new-2"].translation == "第二条注释。"  # a text unit now
    assert state.segments["new-2"].provider_name == "ollama"
    assert report.mapped == 2 and report.finished_not_carried == 0


def test_a_whole_list_whose_items_do_not_match_is_translated_again(tmp_path: Path) -> None:
    old = _segment("old-list", 1, "<li>One.</li><li>Two.</li>", ExtractMode.HTML)
    new = [_segment("new-1", 1, "One."), _segment("new-2", 2, "Two.")]
    state_file = tmp_path / "state.json"
    record = TranslationRecord(segment_id="old-list", translation="<li>一和二。</li>", status=SegmentStatus.COMPLETED)
    save_state(StateDocument(segments={"old-list": record}), state_file)

    report = import_legacy_workspace(tmp_path, state_file, [old], new)

    assert load_state(state_file).segments == {}
    assert report.finished_not_carried == 1
