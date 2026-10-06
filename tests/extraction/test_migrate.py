"""Carrying a workspace from the old segmentation to the new one.

Codex's refute review: old extraction produced (A, A, B) for
<blockquote><p>A</p></blockquote><p>B</p>, the new one produces (A, B); mapping
by order would give B the second A's translation. Matching is by file and text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _done(segment_id: str, translation: str) -> TranslationRecord:
    return TranslationRecord(
        segment_id=segment_id, translation=translation, status=SegmentStatus.COMPLETED
    )


def _audio(segment_id: str) -> str:
    record = {"segment_id": segment_id, "audio_path": "b.m4a"}
    return json.dumps({"session": {}, "segments": {segment_id: record}})


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
                "old-bq": _done("old-bq", "甲"),
                "old-p": _done("old-p", "甲二"),
                "old-b": _done("old-b", "乙"),
            }
        ),
        state_file,
    )
    audio_dir = tmp_path / "audiobook@edgetts"
    audio_dir.mkdir()
    (audio_dir / "audio_state.json").write_text(
        _audio("old-b"),
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


def test_a_unit_that_became_html_keeps_a_translation_that_meets_the_contract(
    tmp_path: Path,
) -> None:
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
    old = _segment(
        "old-list",
        1,
        '<li><a href="#r1">1.</a> First note.</li><li>Second note.</li>',
        ExtractMode.HTML,
    )
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
    record = _done("old-list", "<li>一和二。</li>")
    save_state(StateDocument(segments={"old-list": record}), state_file)

    report = import_legacy_workspace(tmp_path, state_file, [old], new)

    assert load_state(state_file).segments == {}
    assert report.finished_not_carried == 1


def test_an_item_holding_one_paragraph_carries_the_paragraph_text(tmp_path: Path) -> None:
    """Format 5 split a list kept whole before into the paragraphs inside its
    items; each got its item's translation, <p> and all: a <p> inside a <p>."""
    old = _segment(
        "old-list",
        1,
        '<li><p><a href="#r1">1</a> First note.</p></li><li><p>Second.</p></li>',
        ExtractMode.HTML,
    )
    new = [
        _segment("new-1", 1, '<a href="#r1">1</a> First note.', ExtractMode.HTML),
        _segment("new-2", 2, "Second."),
    ]
    state_file = tmp_path / "state.json"
    record = TranslationRecord(
        segment_id="old-list",
        translation='<li><p><a href="#r1">1</a> 第一条。</p></li><li><p>第二条。</p></li>',
        status=SegmentStatus.COMPLETED,
        provider_name="ollama",
    )
    save_state(StateDocument(segments={"old-list": record}), state_file)

    import_legacy_workspace(tmp_path, state_file, [old], new)

    state = load_state(state_file)
    assert state.segments["new-1"].translation == '<a href="#r1">1</a> 第一条。'
    assert state.segments["new-2"].translation == "第二条。"


def test_an_import_interrupted_before_the_segments_were_saved_can_run_again(tmp_path: Path) -> None:
    """Extraction saves the new segments after the import; if it never got that
    far, the next extraction imports again over files already rewritten, and
    must keep what they hold rather than drop it as unmatched."""
    state_file = tmp_path / "state.json"
    save_state(
        StateDocument(
            segments={
                "old-bq": _done("old-bq", "甲"),
                "old-b": _done("old-b", "乙"),
            }
        ),
        state_file,
    )
    audio_dir = tmp_path / "audiobook@edgetts"
    audio_dir.mkdir()
    (audio_dir / "audio_state.json").write_text(
        _audio("old-b"),
        encoding="utf-8",
    )

    import_legacy_workspace(tmp_path, state_file, OLD, NEW)
    first = load_state(state_file).segments
    report = import_legacy_workspace(tmp_path, state_file, OLD, NEW)

    assert load_state(state_file).segments == first
    assert {sid: r.translation for sid, r in first.items()} == {"new-1": "甲", "new-2": "乙"}
    audio = json.loads((audio_dir / "audio_state.json").read_text(encoding="utf-8"))
    assert audio["segments"] == {"new-2": {"segment_id": "new-2", "audio_path": "b.m4a"}}
    assert report.finished_not_carried == 0


def test_an_unreadable_audio_state_leaves_the_translation_state_untouched(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    record = _done("old-b", "乙")
    save_state(StateDocument(segments={"old-b": record}), state_file)
    before = state_file.read_bytes()
    audio_dir = tmp_path / "audiobook@edgetts"
    audio_dir.mkdir()
    (audio_dir / "audio_state.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        import_legacy_workspace(tmp_path, state_file, OLD, NEW)

    assert state_file.read_bytes() == before
    assert list(tmp_path.glob("state.*.json")) == []


def test_a_retry_with_positional_ids_shared_by_old_and_new_units(tmp_path: Path) -> None:
    """Ids are positional in both segmentations, so after an interrupted import
    the rewritten state's ids are also old ids, of other paragraphs. Imported
    again from it, u-1 (now A's translation, once Z's id) was dropped and u-2
    (now B's, once A's id) moved onto A."""
    old = [_segment("u-1", 1, "Z"), _segment("u-2", 2, "A"), _segment("u-3", 3, "B")]
    new = [_segment("u-1", 1, "A"), _segment("u-2", 2, "B")]
    state_file = tmp_path / "state.json"
    save_state(
        StateDocument(
            segments={
                sid: _done(sid, text)
                for sid, text in [("u-1", "Z译"), ("u-2", "甲"), ("u-3", "乙")]
            }
        ),
        state_file,
    )
    audio_dir = tmp_path / "audiobook@edgetts"
    audio_dir.mkdir()
    (audio_dir / "audio_state.json").write_text(
        _audio("u-3"),
        encoding="utf-8",
    )

    first = import_legacy_workspace(tmp_path, state_file, old, new)
    retry = import_legacy_workspace(tmp_path, state_file, old, new)

    translations = {sid: r.translation for sid, r in load_state(state_file).segments.items()}
    assert translations == {"u-1": "甲", "u-2": "乙"}
    audio = json.loads((audio_dir / "audio_state.json").read_text(encoding="utf-8"))
    assert audio["segments"] == {"u-2": {"segment_id": "u-2", "audio_path": "b.m4a"}}
    assert retry.backups == first.backups
    assert retry.finished_not_carried == first.finished_not_carried == 1


def test_a_finished_import_is_not_redone_from_its_backups(tmp_path: Path) -> None:
    from extraction.migrate import finish_legacy_import

    old = [_segment("u-1", 1, "A")]
    new = [_segment("u-1", 1, "A")]
    state_file = tmp_path / "state.json"
    record = _done("u-1", "甲")
    save_state(StateDocument(segments={"u-1": record}), state_file)
    import_legacy_workspace(tmp_path, state_file, old, new)
    finish_legacy_import(tmp_path)
    assert not (tmp_path / "migration.json").exists()

    later = load_state(state_file)
    later.segments["u-1"].translation = "甲改"
    save_state(later, state_file)
    import_legacy_workspace(tmp_path, state_file, old, new)
    assert load_state(state_file).segments["u-1"].translation == "甲改"
