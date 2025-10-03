from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from state.base import load_generic_state, save_generic_state

from .models import AudioSegmentState, AudioSegmentStatus, AudioSessionConfig, AudioStateDocument

# Thread-safe state file operations
_state_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    """Get or create a lock for a specific state file path."""
    path_str = str(path.resolve())
    with _locks_lock:
        if path_str not in _state_file_locks:
            _state_file_locks[path_str] = threading.Lock()
        return _state_file_locks[path_str]


def _default_state(
    output_dir: Path,
    voice: str,
    language: str | None = None,
    cover_path: Path | None = None,
    tts_provider: str = "edge",
    tts_model: str | None = None,
    tts_speed: float = 1.0,
) -> AudioStateDocument:
    session = AudioSessionConfig(
        voice=voice,
        language=language,
        output_dir=output_dir,
        cover_path=cover_path,
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_speed=tts_speed,
    )
    return AudioStateDocument(session=session, segments={})


def load_state(path: Path) -> AudioStateDocument:
    return load_generic_state(path, AudioStateDocument)


def save_state(state: AudioStateDocument, path: Path) -> None:
    lock = _get_lock(path)
    with lock:
        save_generic_state(state, path)


def ensure_state(
    path: Path,
    output_dir: Path,
    voice: str,
    language: str | None = None,
    cover_path: Path | None = None,
    tts_provider: str = "edge",
    tts_model: str | None = None,
    tts_speed: float = 1.0,
) -> AudioStateDocument:
    if path.exists():
        state = load_state(path)
        changed = False
        if language and state.session.language != language:
            state.session.language = language
            changed = True
        if state.session.voice != voice:
            state.session.voice = voice
            changed = True
        if cover_path is not None and state.session.cover_path != cover_path:
            state.session.cover_path = cover_path
            changed = True
        if state.session.tts_provider != tts_provider:
            state.session.tts_provider = tts_provider
            changed = True
        if tts_model is not None and state.session.tts_model != tts_model:
            state.session.tts_model = tts_model
            changed = True
        if state.session.tts_speed != tts_speed:
            state.session.tts_speed = tts_speed
            changed = True
        if changed:
            save_state(state, path)
        return state
    state = _default_state(
        output_dir=output_dir,
        voice=voice,
        language=language,
        cover_path=cover_path,
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_speed=tts_speed,
    )
    save_state(state, path)
    return state


def update_segment_state(
    state_path: Path,
    segment_id: str,
    updater,
) -> AudioSegmentState:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, AudioStateDocument)
        segment = state.segments.get(segment_id)
        if segment is None:
            segment = AudioSegmentState(segment_id=segment_id)
        updated = updater(segment)
        state.segments[segment_id] = updated
        state.segments[segment_id].updated_at = datetime.utcnow()
        save_generic_state(state, state_path)
        return updated


def mark_status(
    state_path: Path,
    segment_id: str,
    status: AudioSegmentStatus,
    **fields,
) -> AudioSegmentState:
    def _updater(segment: AudioSegmentState) -> AudioSegmentState:
        payload = segment.model_dump()
        payload.update(fields)
        payload["status"] = status
        payload.setdefault("segment_id", segment_id)
        return AudioSegmentState.model_validate(payload)

    return update_segment_state(state_path, segment_id, _updater)


def iter_segments_by_status(state: AudioStateDocument, status: AudioSegmentStatus):
    for seg in state.segments.values():
        if seg.status == status:
            yield seg


def get_or_create_segment(state_path: Path, segment_id: str) -> AudioSegmentState:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, AudioStateDocument)
        existing = state.segments.get(segment_id)
        if existing:
            return existing
        new_seg = AudioSegmentState(segment_id=segment_id)
        state.segments[segment_id] = new_seg
        save_generic_state(state, state_path)
        return new_seg


def set_consecutive_failures(state_path: Path, count: int) -> None:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, AudioStateDocument)
        state.consecutive_failures = count
        save_generic_state(state, state_path)


def set_cooldown(state_path: Path, until: datetime | None) -> None:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, AudioStateDocument)
        state.cooldown_until = until
        save_generic_state(state, state_path)


def reset_error_segments(state_path: Path, segment_ids: list[str] | None = None) -> list[str]:
    lock = _get_lock(state_path)
    with lock:
        state = load_generic_state(state_path, AudioStateDocument)
        changed = False
        reset_ids: list[str] = []
        target_ids = set(segment_ids) if segment_ids else None
        for seg_id, segment in state.segments.items():
            if target_ids is not None and seg_id not in target_ids:
                continue
            if segment.status == AudioSegmentStatus.ERROR:
                segment.status = AudioSegmentStatus.PENDING
                segment.attempts = 0
                segment.updated_at = datetime.utcnow()
                state.segments[seg_id] = segment
                reset_ids.append(seg_id)
                changed = True
        if changed:
            state.consecutive_failures = 0
            save_generic_state(state, state_path)
        return reset_ids
