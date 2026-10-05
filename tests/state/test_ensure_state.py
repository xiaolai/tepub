"""Switching provider or model must not erase finished translations.

ensure_state rebuilt the whole state as PENDING whenever the provider or model
differed from the recorded one, with no warning and no copy, so trying a second
model discarded a book's worth of paid-for work.
"""

from __future__ import annotations

from pathlib import Path

from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus
from state.store import backup_state, ensure_state, load_state, mark_status


def _segments(n: int = 3) -> list[Segment]:
    return [
        Segment(
            segment_id=f"s{i}",
            file_path=Path("c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, n + 1)
    ]


def _translated_state(path: Path) -> None:
    ensure_state(path, _segments(), "openai", "gpt-a", "en", "zh")
    mark_status(path, "s1", SegmentStatus.COMPLETED, translation="一", provider_name="openai", model_name="gpt-a")


def test_a_new_model_keeps_finished_translations(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _translated_state(path)

    state = ensure_state(path, _segments(), "anthropic", "claude-x", "en", "zh")

    assert state.segments["s1"].status == SegmentStatus.COMPLETED
    assert state.segments["s1"].translation == "一"
    assert state.segments["s1"].model_name == "gpt-a"  # who translated it is kept
    assert (state.current_provider, state.current_model) == ("anthropic", "claude-x")
    assert load_state(path).current_model == "claude-x"


def test_backup_state_copies_the_file_beside_it(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _translated_state(path)

    backup = backup_state(path)

    assert backup.parent == tmp_path and backup != path
    assert load_state(backup).segments["s1"].translation == "一"
