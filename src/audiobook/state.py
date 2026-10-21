from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from console_singleton import get_console
from state.base import load_generic_state, save_generic_state

from .models import AudioSegmentState, AudioSegmentStatus, AudioSessionConfig, AudioStateDocument

console = get_console()

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


def _journal_path(path: Path) -> Path:
    """Changes recorded since the last whole-document write."""
    return path.with_name(f"{path.name}.journal")


def _journal_patches(path: Path) -> list[dict]:
    journal = _journal_path(path)
    if not journal.exists():
        return []
    # Bytes, not text: a write torn inside a multi-byte character is torn too,
    # and read_text would raise on it rather than let the last line be ignored.
    # Split on the writer's newline only: bytes.splitlines would also split at
    # 0x85, which is an ordinary continuation byte in UTF-8 text.
    lines = journal.read_bytes().split(b"\n")
    while lines and not lines[-1].strip():
        lines.pop()
    patches: list[dict] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            patches.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # A crash can tear the line being written; every earlier one is
            # complete. A torn line anywhere else is corruption, not recovery.
            if index == len(lines) - 1:
                break
            raise
    return patches


def _apply_journal(state: AudioStateDocument, path: Path) -> AudioStateDocument:
    """Replay changes appended since the last whole-document write.

    Linear in the journal, each record read and applied once. Idempotent:
    every record assigns the fields it carries, so replaying it twice over
    the same base document gives the same state.
    """
    for patch in _journal_patches(path):
        for segment_id, payload in (patch.get("segments") or {}).items():
            state.segments[segment_id] = AudioSegmentState.model_validate(payload)
        if "consecutive_failures" in patch:
            state.consecutive_failures = patch["consecutive_failures"]
        if "cooldown_until" in patch:
            value = patch["cooldown_until"]
            state.cooldown_until = datetime.fromisoformat(value) if value else None
    return state


def _last_record_bounds(handle, size: int) -> tuple[int, int]:
    """Byte range of the journal's last non-empty line, without its newline."""
    end = size
    while end > 0:
        handle.seek(end - 1)
        if handle.read(1) not in (b"\n", b"\r"):
            break
        end -= 1
    start = end
    while start > 0:
        chunk_start = max(0, start - 8192)
        handle.seek(chunk_start)
        index = handle.read(start - chunk_start).rfind(b"\n")
        if index != -1:
            return chunk_start + index + 1, end
        start = chunk_start
    return 0, end


def _complete_journal_tail(journal: Path) -> None:
    """Make the journal's tail whole before a new record is appended.

    A crash can leave the last record half-written, and replay ignores it only
    because it is last. Appending after a missing newline fused the new record
    to the torn one, so the result was no longer last and every later replay
    raised instead of ignoring it. A record that is whole but lost its newline
    is kept. A whitespace-only line is not a record, so it is dropped too:
    truncating only it left a torn record in front of the appended one, which
    made that record non-final and raised on the next load. Only the tail is
    read, so appending stays linear.
    """
    if not journal.exists():
        return
    with journal.open("r+b") as handle:
        while True:
            size = handle.seek(0, 2)
            start, end = _last_record_bounds(handle, size)
            if start == end:
                return
            handle.seek(end)
            tail = handle.read(size - end)
            handle.seek(start)
            try:
                json.loads(handle.read(end - start))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # A torn record, or a whitespace-only line after one. Drop it
                # and look at what precedes it; that line may be torn as well.
                handle.truncate(start)
                continue
            # Records are split on LF during replay. A bare CR ends no record,
            # so the next one would fuse to this line and both would be lost.
            if b"\n" not in tail:
                handle.seek(size)
                handle.write(b"\n")
            return


def _append_journal(path: Path, patch: dict) -> None:
    journal = _journal_path(path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    _complete_journal_tail(journal)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(patch, ensure_ascii=False) + "\n")
        handle.flush()


def _write_state(state: AudioStateDocument, path: Path) -> None:
    """Write the whole document and drop the journal it now contains.

    Callers that already hold ``_get_lock`` use this directly; ``save_state``
    wraps it for the rest.
    """
    save_generic_state(state, path)
    _journal_path(path).unlink(missing_ok=True)


def _default_state(
    output_dir: Path,
    voice: str,
    language: str | None = None,
    cover_path: Path | None = None,
    tts_provider: str = "edge",
    tts_model: str | None = None,
    tts_speed: float | None = None,
    rate: str | None = None,
    volume: str | None = None,
) -> AudioStateDocument:
    session = AudioSessionConfig(
        voice=voice,
        language=language,
        output_dir=output_dir,
        cover_path=cover_path,
        tts_provider=tts_provider,
        tts_model=tts_model,
        # None means "not chosen"; the session model holds the default.
        **({"tts_speed": tts_speed} if tts_speed is not None else {}),
        rate=rate,
        volume=volume,
    )
    return AudioStateDocument(session=session, segments={})


def load_state(path: Path) -> AudioStateDocument:
    return _apply_journal(load_generic_state(path, AudioStateDocument), path)


def save_state(state: AudioStateDocument, path: Path) -> None:
    lock = _get_lock(path)
    with lock:
        _write_state(state, path)


class AudioStateWriter:
    """The audio state held in memory for a run, persisted as a journal.

    Recording a result used to read and rewrite the whole document, and a
    success then rewrote it again to clear the failure counter. The document
    grows with every segment, so a long book did O(segments) JSON work per
    result on the main thread. Each change is now one appended line, and the
    document is written whole once, when the block exits — success, failure or
    interrupt alike. Fixed-size whole-document checkpoints were still
    quadratic: every batch serialised everything recorded so far.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.doc = load_state(path)
        self._changed = False

    def __enter__(self) -> AudioStateWriter:
        return self

    def __exit__(self, *exc_info) -> None:
        self.flush()

    def mark(
        self, segment_id: str, status: AudioSegmentStatus, **fields
    ) -> AudioSegmentState:
        record = self.doc.segments.get(segment_id)
        if record is None:
            record = AudioSegmentState(segment_id=segment_id)
        payload = record.model_dump()
        payload.update(fields)
        payload["status"] = status
        payload["updated_at"] = datetime.now(timezone.utc)
        updated = AudioSegmentState.model_validate(payload)
        self.doc.segments[segment_id] = updated
        self._record({"segments": {segment_id: updated.model_dump(mode="json")}})
        return updated

    @property
    def consecutive_failures(self) -> int:
        return self.doc.consecutive_failures

    @consecutive_failures.setter
    def consecutive_failures(self, count: int) -> None:
        if count != self.doc.consecutive_failures:
            self.doc.consecutive_failures = count
            self._record({"consecutive_failures": count})

    def set_cooldown(self, until: datetime | None) -> None:
        self.doc.cooldown_until = until
        self._record({"cooldown_until": until.isoformat() if until else None})
        # A cooldown is long; write it whole now rather than leave it in the
        # journal alone.
        self.flush()

    def flush(self) -> None:
        """Write the journal's changes whole, then drop the journal."""
        if self._changed:
            save_state(self.doc, self.path)
            self._changed = False

    def _record(self, patch: dict) -> None:
        _append_journal(self.path, patch)
        self._changed = True


#: Session settings that change the rendered audio. A change to any of them makes
#: previously synthesised segments stale. cover_path is deliberately excluded: it
#: affects only the container artwork, not the audio.
_AUDIO_AFFECTING_FIELDS = (
    "voice",
    "language",
    "tts_provider",
    "tts_model",
    "tts_speed",
    "rate",
    "volume",
)


def _invalidate_completed_segments(state: AudioStateDocument) -> int:
    """Reset completed segments so they are re-synthesised. Returns the count."""
    reset = 0
    for segment in state.segments.values():
        if segment.status == AudioSegmentStatus.COMPLETED:
            segment.status = AudioSegmentStatus.PENDING
            segment.audio_path = None
            segment.duration_seconds = None
            reset += 1
    return reset


def ensure_state(
    path: Path,
    output_dir: Path,
    voice: str,
    language: str | None = None,
    cover_path: Path | None = None,
    tts_provider: str = "edge",
    tts_model: str | None = None,
    tts_speed: float | None = None,
    rate: str | None = None,
    volume: str | None = None,
) -> AudioStateDocument:
    if path.exists():
        state = load_state(path)
        changed = False
        # Snapshot before mutation so an audio-affecting change can be detected.
        before = {f: getattr(state.session, f, None) for f in _AUDIO_AFFECTING_FIELDS}
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
        # None (no --tts-speed, nothing configured) keeps the stored speed.
        if tts_speed is not None and state.session.tts_speed != tts_speed:
            state.session.tts_speed = tts_speed
            changed = True
        if rate is not None and state.session.rate != rate:
            state.session.rate = rate
            changed = True
        if volume is not None and state.session.volume != volume:
            state.session.volume = volume
            changed = True

        # Session metadata used to be updated while every completed segment stayed
        # reusable, so switching voice, model, speed or language silently produced
        # a book made of the *previous* settings' audio.
        after = {f: getattr(state.session, f, None) for f in _AUDIO_AFFECTING_FIELDS}
        if before != after:
            reset = _invalidate_completed_segments(state)
            if reset:
                changed = True
                console.print(
                    f"[yellow]Audio settings changed; re-synthesising {reset} "
                    f"previously completed segments.[/yellow]"
                )

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
        rate=rate,
        volume=volume,
    )
    save_state(state, path)
    return state


def apply_text_digests(state_path: Path, digests: dict[str, str]) -> list[str]:
    """Check completed audio against the text it must speak; returns reset IDs.

    A completed segment whose recorded digest differs from ``digests`` is reset
    to pending, so its audio is synthesised again. One with no recorded digest
    predates the field: nothing proves its audio was made from the current
    text, and backfilling the current digest kept stale narration after an
    edit, marked as current. It is re-synthesised once and stamped from then on.
    """
    lock = _get_lock(state_path)
    with lock:
        state = _apply_journal(load_generic_state(state_path, AudioStateDocument), state_path)
        changed = False
        reset_ids: list[str] = []
        for seg_id, digest in digests.items():
            segment = state.segments.get(seg_id)
            if segment is None or segment.status != AudioSegmentStatus.COMPLETED:
                continue
            if segment.text_sha256 != digest:
                segment.status = AudioSegmentStatus.PENDING
                segment.audio_path = None
                segment.duration_seconds = None
                segment.text_sha256 = None
                segment.updated_at = datetime.now(timezone.utc)
                reset_ids.append(seg_id)
                changed = True
        if changed:
            _write_state(state, state_path)
        return reset_ids


def update_segment_state(
    state_path: Path,
    segment_id: str,
    updater,
) -> AudioSegmentState:
    lock = _get_lock(state_path)
    with lock:
        state = _apply_journal(load_generic_state(state_path, AudioStateDocument), state_path)
        segment = state.segments.get(segment_id)
        if segment is None:
            segment = AudioSegmentState(segment_id=segment_id)
        updated = updater(segment)
        state.segments[segment_id] = updated
        state.segments[segment_id].updated_at = datetime.now(timezone.utc)
        _write_state(state, state_path)
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


def set_consecutive_failures(state_path: Path, count: int) -> None:
    lock = _get_lock(state_path)
    with lock:
        state = _apply_journal(load_generic_state(state_path, AudioStateDocument), state_path)
        state.consecutive_failures = count
        _write_state(state, state_path)


def set_cooldown(state_path: Path, until: datetime | None) -> None:
    lock = _get_lock(state_path)
    with lock:
        state = _apply_journal(load_generic_state(state_path, AudioStateDocument), state_path)
        state.cooldown_until = until
        _write_state(state, state_path)


def reset_error_segments(state_path: Path, segment_ids: list[str] | None = None) -> list[str]:
    lock = _get_lock(state_path)
    with lock:
        state = _apply_journal(load_generic_state(state_path, AudioStateDocument), state_path)
        changed = False
        reset_ids: list[str] = []
        target_ids = set(segment_ids) if segment_ids else None
        for seg_id, segment in state.segments.items():
            if target_ids is not None and seg_id not in target_ids:
                continue
            if segment.status == AudioSegmentStatus.ERROR:
                segment.status = AudioSegmentStatus.PENDING
                segment.attempts = 0
                segment.updated_at = datetime.now(timezone.utc)
                state.segments[seg_id] = segment
                reset_ids.append(seg_id)
                changed = True
        if changed:
            state.consecutive_failures = 0
            _write_state(state, state_path)
        return reset_ids
