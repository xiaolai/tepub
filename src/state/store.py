from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .base import load_generic_state, save_generic_state, state_file_lock
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
    segment_ids = {seg.segment_id for seg in segments_list}

    if path.exists() and not force_reset:
        existing = load_state(path)
        if (
            existing.current_provider == provider
            and existing.current_model == model
            and existing.source_language == source_language
            and existing.target_language == target_language
        ):
            # Sync segments: add missing, keep existing translations
            existing_ids = set(existing.segments.keys())
            missing_ids = segment_ids - existing_ids

            if missing_ids:
                # Add new segments that weren't in the state
                for seg in segments_list:
                    if seg.segment_id in missing_ids:
                        existing.segments[seg.segment_id] = TranslationRecord(
                            segment_id=seg.segment_id
                        )
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


def update_translation_record(state_path: Path, segment_id: str, updater) -> TranslationRecord:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, StateDocument)
        record = state.segments.get(segment_id)
        if record is None:
            raise KeyError(f"Segment {segment_id} missing from state file")

        updated = updater(record)
        state.segments[segment_id] = updated
        save_generic_state(state, state_path)
        return updated


def mark_status(
    state_path: Path, segment_id: str, status: SegmentStatus, **fields
) -> TranslationRecord:
    def _updater(record: TranslationRecord) -> TranslationRecord:
        payload = record.model_dump()
        payload.update(fields)
        payload["status"] = status
        return TranslationRecord.model_validate(payload)

    return update_translation_record(state_path, segment_id, _updater)


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


def iter_pending_segments(state: StateDocument) -> Iterable[str]:
    for segment_id, record in state.segments.items():
        if record.status == SegmentStatus.PENDING:
            yield segment_id


def iter_segments_by_status(state: StateDocument, status: SegmentStatus) -> Iterable[str]:
    for segment_id, record in state.segments.items():
        if record.status == status:
            yield segment_id


def _set_state_field(state_path: Path, field: str, value) -> None:
    """Assign one top-level state field under both locks.

    set_consecutive_failures and set_cooldown were byte-identical apart from the
    field they assigned.
    """
    with _get_lock(state_path), state_file_lock(state_path):
        state = load_generic_state(state_path, StateDocument)
        setattr(state, field, value)
        save_generic_state(state, state_path)


def set_consecutive_failures(state_path: Path, count: int) -> None:
    """Set the consecutive failures counter in the state file."""
    _set_state_field(state_path, "consecutive_failures", count)


def set_cooldown(state_path: Path, until: datetime | None) -> None:
    """Set the cooldown expiration timestamp in the state file."""
    _set_state_field(state_path, "cooldown_until", until)


def reset_error_segments(state_path: Path, segment_ids: list[str] | None = None) -> list[str]:
    """Reset ERROR segments to PENDING for retry.

    Args:
        state_path: Path to state file
        segment_ids: Optional list of specific segment IDs to reset. If None, resets all ERROR segments.

    Returns:
        List of segment IDs that were reset
    """
    with _get_lock(state_path), state_file_lock(state_path):
        state = load_generic_state(state_path, StateDocument)
        changed = False
        reset_ids: list[str] = []
        # `if segment_ids` treats an explicit empty list as "no filter", so
        # reset_error_segments(path, []) reset *every* errored segment.
        target_ids = None if segment_ids is None else set(segment_ids)

        for seg_id, record in state.segments.items():
            if target_ids is not None and seg_id not in target_ids:
                continue
            if record.status == SegmentStatus.ERROR:
                record.status = SegmentStatus.PENDING
                record.error_message = None
                changed = True
                reset_ids.append(seg_id)

        if changed:
            save_generic_state(state, state_path)

        return reset_ids


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
