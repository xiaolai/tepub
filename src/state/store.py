from __future__ import annotations

import shutil
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .base import load_generic_state, save_generic_state
from .models import (
    ResumeInfo,
    Segment,
    SegmentsDocument,
    SegmentStatus,
    StateDocument,
    TranslationRecord,
)

# Thread-safe state file operations
_state_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    """Get or create an in-process lock for a specific state file path.

    This serialises threads only. Cross-process safety comes from
    state.base.state_file_lock, which the transactional helpers below combine
    with this one; the in-memory lock alone left concurrent tepub processes free
    to interleave read-modify-write cycles.
    """
    path_str = str(path.resolve())
    with _locks_lock:
        if path_str not in _state_file_locks:
            _state_file_locks[path_str] = threading.Lock()
        return _state_file_locks[path_str]


def save_segments(document: SegmentsDocument, path: Path) -> None:
    save_generic_state(document, path)


def load_segments(path: Path) -> SegmentsDocument:
    return load_generic_state(path, SegmentsDocument)


def save_state(document: StateDocument, path: Path) -> None:
    lock = _get_lock(path)
    with lock:
        save_generic_state(document, path)


def load_state(path: Path) -> StateDocument:
    return load_generic_state(path, StateDocument)


def ensure_state(
    path: Path,
    segments: Iterable[Segment],
    provider: str,
    model: str,
    source_language: str,
    target_language: str,
    force_reset: bool = False,
) -> StateDocument:
    segments_list = list(segments)  # Consume iterable once

    if path.exists() and not force_reset:
        existing = load_state(path)
        # Only the languages decide whether finished work still applies. A change
        # of provider or model used to rebuild the whole state as PENDING, with no
        # warning and no copy; each record already says who translated it.
        if (
            existing.source_language == source_language
            and existing.target_language == target_language
        ):
            changed = False
            for seg in segments_list:
                if seg.segment_id not in existing.segments:
                    existing.segments[seg.segment_id] = TranslationRecord(segment_id=seg.segment_id)
                    changed = True
            if (existing.current_provider, existing.current_model) != (provider, model):
                existing.current_provider = provider
                existing.current_model = model
                changed = True
            if changed:
                save_state(existing, path)
            return existing

    doc = StateDocument(
        segments={
            segment.segment_id: TranslationRecord(segment_id=segment.segment_id)
            for segment in segments_list
        },
        current_provider=provider,
        current_model=model,
        source_language=source_language,
        target_language=target_language,
    )
    save_state(doc, path)
    return doc


def backup_state(path: Path) -> Path:
    """Copy ``path`` beside itself with a UTC timestamp, before it is reset."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def compute_resume_info(state: StateDocument) -> ResumeInfo:
    remaining, completed, skipped = [], [], []
    for record in state.segments.values():
        if record.status == SegmentStatus.COMPLETED:
            completed.append(record.segment_id)
        elif record.status == SegmentStatus.SKIPPED:
            skipped.append(record.segment_id)
        else:
            remaining.append(record.segment_id)
    return ResumeInfo(
        remaining_segments=sorted(remaining),
        completed_segments=sorted(completed),
        skipped_segments=sorted(skipped),
    )


def update_state_atomic(state_path: Path, updater) -> bool:
    """Run ``updater(state)`` under the state-file lock and persist any change.

    Commands that did load_state -> modify -> save_state left a window in which a
    concurrent translate run could write between the read and the write, and its
    updates were then overwritten wholesale. ``updater`` receives the freshly
    loaded document and returns the document to save, or None to make no change.

    Returns True when the file was rewritten.
    """
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, StateDocument)
        # Snapshot before calling the updater: an updater that mutates the document
        # in place and returns it would otherwise be compared against itself, so
        # the change would always look like a no-op and never be persisted.
        before = state.model_dump()
        updated = updater(state)
        if updated is None:
            return False
        if updated.model_dump() == before:
            return False
        save_generic_state(updated, state_path)
        return True
