"""Translation state lives in memory during a run, with one writer.

Every result used to rewrite the whole state file twice (mark_status, then
set_consecutive_failures), each a full read and a full write under a lock. On a
5000-segment book that was about 0.4 s per segment, serialised on the main
thread, so the number of workers barely mattered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exceptions import WorkspaceBusyError
from state import store
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus
from state.store import ensure_state, load_state
from state.writer import StateWriter, exclusive_run


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
def state_path(tmp_path: Path) -> Path:
    path = tmp_path / "state.json"
    ensure_state(path, _segments(500), "p", "m", "en", "zh")
    return path


def test_writes_are_batched(state_path: Path, monkeypatch) -> None:
    writes: list[int] = []
    real = store.save_state
    monkeypatch.setattr("state.writer.save_state", lambda doc, path: (writes.append(1), real(doc, path)))

    with StateWriter(state_path, flush_every=50, flush_interval=3600) as writer:
        for i in range(1, 501):
            writer.mark(f"s{i}", SegmentStatus.COMPLETED, translation=f"t{i}")

    assert len(writes) <= 500 // 50 + 1
    final = load_state(state_path)
    assert all(r.status == SegmentStatus.COMPLETED for r in final.segments.values())


def test_a_crash_loses_at_most_one_batch(state_path: Path) -> None:
    writer = StateWriter(state_path, flush_every=10, flush_interval=3600)
    for i in range(1, 26):
        writer.mark(f"s{i}", SegmentStatus.COMPLETED, translation=f"t{i}")
    # No flush and no exit: the process died here.
    on_disk = load_state(state_path)
    done = sum(1 for r in on_disk.segments.values() if r.status == SegmentStatus.COMPLETED)
    assert done == 20


def test_leaving_the_block_flushes_even_on_interrupt(state_path: Path) -> None:
    with pytest.raises(KeyboardInterrupt):
        with StateWriter(state_path, flush_every=1000, flush_interval=3600) as writer:
            writer.mark("s1", SegmentStatus.COMPLETED, translation="t1")
            raise KeyboardInterrupt
    assert load_state(state_path).segments["s1"].status == SegmentStatus.COMPLETED


def test_time_also_triggers_a_flush(state_path: Path) -> None:
    now = [0.0]
    writer = StateWriter(state_path, flush_every=1000, flush_interval=5.0, clock=lambda: now[0])
    writer.mark("s1", SegmentStatus.COMPLETED, translation="t1")
    assert load_state(state_path).segments["s1"].status == SegmentStatus.PENDING
    now[0] = 6.0
    writer.mark("s2", SegmentStatus.COMPLETED, translation="t2")
    assert load_state(state_path).segments["s2"].status == SegmentStatus.COMPLETED


def test_reset_errors_and_counters_are_in_memory(state_path: Path) -> None:
    with StateWriter(state_path) as writer:
        writer.mark("s1", SegmentStatus.ERROR, error_message="boom")
        writer.consecutive_failures = 2
        assert writer.reset_errors(["s1"]) == ["s1"]
        assert writer.doc.segments["s1"].status == SegmentStatus.PENDING
    final = load_state(state_path)
    assert final.consecutive_failures == 2 and final.segments["s1"].error_message is None


def test_an_unknown_segment_is_an_error(state_path: Path) -> None:
    with StateWriter(state_path) as writer:
        with pytest.raises(KeyError, match="nope"):
            writer.mark("nope", SegmentStatus.COMPLETED)


def test_a_second_run_on_the_same_workspace_is_refused(state_path: Path) -> None:
    with exclusive_run(state_path):
        with pytest.raises(WorkspaceBusyError, match="another tepub"):
            with exclusive_run(state_path):
                pass
    with exclusive_run(state_path):  # released again afterwards
        pass
