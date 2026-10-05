"""One in-memory writer for translation state during a run.

Each result used to be saved by reading and rewriting the whole state file, and
the failure counter by doing it again, so a 5000-segment book spent about 0.4 s
of main-thread time per segment on JSON. The run now holds the document in
memory and writes it every ``flush_every`` changes or ``flush_interval``
seconds, and always when the run ends, fails or is interrupted. A crash loses
at most one unflushed batch, and those segments are simply translated again.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from exceptions import WorkspaceBusyError

from .base import HAS_PORTALOCKER
from .models import SegmentStatus, StateDocument, TranslationRecord
from .store import load_state, save_state

if HAS_PORTALOCKER:
    import portalocker


class StateWriter:
    def __init__(
        self,
        path: Path,
        *,
        flush_every: int = 50,
        flush_interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.doc: StateDocument = load_state(path)
        self._flush_every = flush_every
        self._flush_interval = flush_interval
        self._clock = clock
        self._dirty = 0
        self._last_flush = clock()

    def __enter__(self) -> StateWriter:
        return self

    def __exit__(self, *exc_info) -> None:
        # Runs on success, on error and on KeyboardInterrupt alike.
        self.flush()

    def mark(self, segment_id: str, status: SegmentStatus, **fields) -> TranslationRecord:
        record = self.doc.segments.get(segment_id)
        if record is None:
            raise KeyError(f"Segment {segment_id} missing from state file {self.path}")
        payload = record.model_dump()
        payload.update(fields)
        payload["status"] = status
        updated = TranslationRecord.model_validate(payload)
        self.doc.segments[segment_id] = updated
        self._changed()
        return updated

    def reset_errors(self, segment_ids: Iterable[str]) -> list[str]:
        reset: list[str] = []
        for segment_id in segment_ids:
            record = self.doc.segments.get(segment_id)
            if record is not None and record.status == SegmentStatus.ERROR:
                record.status = SegmentStatus.PENDING
                record.error_message = None
                reset.append(segment_id)
        if reset:
            self._changed()
        return reset

    @property
    def consecutive_failures(self) -> int:
        return self.doc.consecutive_failures

    @consecutive_failures.setter
    def consecutive_failures(self, count: int) -> None:
        if count != self.doc.consecutive_failures:
            self.doc.consecutive_failures = count
            self._changed()

    def set_cooldown(self, until: datetime | None) -> None:
        self.doc.cooldown_until = until
        # A cooldown is long; make it visible on disk straight away.
        self._changed()
        self.flush()

    def flush(self) -> None:
        if self._dirty:
            save_state(self.doc, self.path)
            self._dirty = 0
        self._last_flush = self._clock()

    def _changed(self) -> None:
        self._dirty += 1
        if (
            self._dirty >= self._flush_every
            or self._clock() - self._last_flush >= self._flush_interval
        ):
            self.flush()


def _run_lock_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + ".run.lock")


@contextmanager
def exclusive_run(state_path: Path) -> Iterator[None]:
    """Refuse to start while another run holds this workspace.

    Two runs on one workspace used to translate every segment twice, and the
    last writer won. The lock is held for the whole run, released on exit.
    """
    if not HAS_PORTALOCKER:  # pragma: no cover - declared dependency
        yield
        return
    lock_path = _run_lock_path(state_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(lock_path, "a", timeout=0, fail_when_locked=True)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as exc:
        raise WorkspaceBusyError(state_path) from exc
    try:
        yield
    finally:
        lock.release()
