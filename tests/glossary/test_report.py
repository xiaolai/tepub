"""Finding finished translations that do not follow the glossary."""

from __future__ import annotations

from pathlib import Path

from glossary import Glossary, Term
from glossary.report import find_misses, mark_for_retranslation
from state.models import (
    ExtractMode,
    Segment,
    SegmentMetadata,
    SegmentStatus,
    StateDocument,
    TranslationRecord,
)

GLOSSARY = Glossary(
    target_language="zh-CN",
    terms=[Term(source="compound", target="诈骗园区", avoid=["集中营"])],
)


def _segment(segment_id: str, text: str) -> Segment:
    return Segment(
        segment_id=segment_id,
        file_path=Path("c.xhtml"),
        xpath="/x",
        extract_mode=ExtractMode.TEXT,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


def _record(segment_id, translation, status=SegmentStatus.COMPLETED, provider="ollama"):
    return TranslationRecord(
        segment_id=segment_id, translation=translation, status=status, provider_name=provider
    )


SEGMENTS = [
    _segment("ok", "A compound."),
    _segment("avoided", "Another compound."),
    _segment("missing", "The compounds."),
    _segment("pending", "A compound again."),
    _segment("copied", "compound"),
    _segment("no-term", "Nothing here."),
]
STATE = StateDocument(
    segments={
        "ok": _record("ok", "一个诈骗园区。"),
        "avoided": _record("avoided", "另一个集中营。"),
        "missing": _record("missing", "那些地方。"),
        "pending": _record("pending", None, status=SegmentStatus.PENDING),
        "copied": _record("copied", "compound", provider=None),
        "no-term": _record("no-term", "这里什么都没有。"),
    }
)


def test_only_finished_model_translations_are_checked() -> None:
    misses = find_misses(SEGMENTS, STATE, GLOSSARY)
    assert [(m.segment_id, m.problem) for m in misses] == [
        ("avoided", '"compound" must be 诈骗园区, not 集中营'),
        ("missing", '"compound" must be 诈骗园区'),
    ]


def test_marked_units_become_pending_and_lose_their_error() -> None:
    state = STATE.model_copy(deep=True)
    state.segments["missing"] = state.segments["missing"].model_copy(update={"error_message": "x"})
    updated = mark_for_retranslation(state, {"missing", "gone"})
    assert updated.segments["missing"].status == SegmentStatus.PENDING
    assert updated.segments["missing"].error_message is None
    assert updated.segments["ok"].status == SegmentStatus.COMPLETED
