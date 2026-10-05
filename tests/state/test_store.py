"""The state store's remaining surface: resume summary and atomic updates."""

from __future__ import annotations

from pathlib import Path

import pytest

from exceptions import WorkspaceBusyError
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus
from state.resume import load_resume_info
from state.store import ensure_state, load_state
from state.writer import StateWriter, exclusive_run, update_state_atomic


def _segments(n: int) -> list[Segment]:
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


@pytest.fixture
def path(tmp_path: Path) -> Path:
    state_path = tmp_path / "state.json"
    ensure_state(state_path, _segments(4), "p", "m", "en", "zh")
    with StateWriter(state_path) as writer:
        writer.mark("s1", SegmentStatus.COMPLETED, translation="一")
        writer.mark("s2", SegmentStatus.SKIPPED)
        writer.mark("s3", SegmentStatus.ERROR, error_message="boom")
    return state_path


def test_resume_info_sorts_segments_by_outcome(path: Path) -> None:
    info = load_resume_info(path)
    assert info.completed_segments == ["s1"]
    assert info.skipped_segments == ["s2"]
    assert info.remaining_segments == ["s3", "s4"]  # errors are still to do


def test_resume_info_for_a_missing_file_is_empty(tmp_path: Path) -> None:
    info = load_resume_info(tmp_path / "absent.json")
    assert (info.completed_segments, info.remaining_segments) == ([], [])


def test_update_state_atomic_persists_an_in_place_change(path: Path) -> None:
    def clear_error(state):
        state.segments["s3"].error_message = None
        return state

    assert update_state_atomic(path, clear_error) is True
    assert load_state(path).segments["s3"].error_message is None


def test_update_state_atomic_reports_no_change(path: Path) -> None:
    assert update_state_atomic(path, lambda state: state) is False
    assert update_state_atomic(path, lambda state: None) is False


def test_update_state_atomic_refuses_while_a_run_holds_the_workspace(path: Path) -> None:
    """format or purge-refusals during a translate run would overwrite its work."""
    with exclusive_run(path):
        with pytest.raises(WorkspaceBusyError):
            update_state_atomic(path, lambda state: state)
