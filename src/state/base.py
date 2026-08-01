"""Generic state management base for translation and audiobook state.

This module provides type-safe, reusable state operations that can be
shared across different state management systems in the application.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from exceptions import CorruptedStateError

try:
    import portalocker
    HAS_PORTALOCKER = True
except ImportError:
    HAS_PORTALOCKER = False

# Generic type variable for state documents
TDocument = TypeVar("TDocument", bound=BaseModel)


# Locks are keyed to a sidecar file next to the target, not to the temporary file.
# The previous code locked the shared ".tmp" path, released the lock, and only
# then replaced the target — so two writers could lock different inodes, clobber
# each other's temporary file, or move a half-written file into place.
_HELD_LOCKS = threading.local()


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


@contextmanager
def state_file_lock(path: Path):
    """Hold an exclusive cross-process lock for ``path``.

    Re-entrant within a thread so a locked transaction can call the ordinary
    save helpers without deadlocking on itself.
    """
    held = getattr(_HELD_LOCKS, "paths", None)
    if held is None:
        held = set()
        _HELD_LOCKS.paths = held

    key = str(path.resolve())
    if key in held or not HAS_PORTALOCKER:
        # Already held by this thread, or no cross-process locking available.
        yield
        return

    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    held.add(key)
    try:
        with portalocker.Lock(lock_file, "a", timeout=30):
            yield
    finally:
        held.discard(key)


def atomic_write(path: Path, payload: dict) -> None:
    """
    Atomically write a dictionary to a JSON file with file locking.

    Uses a temporary file with .tmp suffix to ensure atomicity.
    The temporary file is written first, then atomically renamed to
    the target path, preventing corruption on crashes or interrupts.

    File locking prevents concurrent writes from multiple processes
    corrupting the state file during parallel operations.

    Args:
        path: Target file path
        payload: Dictionary to serialize as JSON

    Example:
        >>> atomic_write(Path("state.json"), {"key": "value"})
    """
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)

    with state_file_lock(path):
        # A unique temporary file per writer: the shared ".tmp" name meant
        # concurrent writers overwrote each other's pending content.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Replacement happens while the lock is still held.
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


def load_generic_state(path: Path, model_class: type[TDocument]) -> TDocument:
    """
    Load and deserialize a state document from JSON file.

    Args:
        path: Path to JSON state file
        model_class: Pydantic model class for deserialization

    Returns:
        Deserialized state document instance

    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If the file contains invalid JSON
        pydantic.ValidationError: If data doesn't match model schema

    Example:
        >>> from state.models import StateDocument
        >>> state = load_generic_state(Path("state.json"), StateDocument)
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return model_class.model_validate(data)


def save_generic_state(document: TDocument, path: Path) -> None:
    """
    Serialize and atomically save a state document to JSON file.

    Args:
        document: Pydantic model instance to save
        path: Target file path

    Example:
        >>> from state.models import StateDocument
        >>> state = StateDocument(segments={}, ...)
        >>> save_generic_state(state, Path("state.json"))
    """
    payload = json.loads(document.model_dump_json(indent=2))
    atomic_write(path, payload)


def update_state_item(
    state_path: Path,
    model_class: type[TDocument],
    updater: Callable[[TDocument], TDocument],
) -> TDocument:
    """
    Load state, apply update function, and save atomically.

    This is a generic read-modify-write operation that ensures
    atomicity through file locking via atomic_write.

    Args:
        state_path: Path to state file
        model_class: Pydantic model class for the state document
        updater: Function that takes current state and returns updated state

    Returns:
        Updated state document

    Example:
        >>> def increment_counter(state: StateDocument) -> StateDocument:
        ...     state.counter += 1
        ...     return state
        >>> updated = update_state_item(
        ...     Path("state.json"),
        ...     StateDocument,
        ...     increment_counter
        ... )
    """
    # The whole read-modify-write runs under one lock. Without it, concurrent
    # updates read the same state and the later save discarded the earlier one.
    with state_file_lock(state_path):
        state = load_generic_state(state_path, model_class)
        updated_state = updater(state)
        save_generic_state(updated_state, state_path)
        return updated_state


def safe_load_state(
    path: Path,
    model_class: type[TDocument],
    state_type: str = "state",
) -> TDocument:
    """
    Safely load state with graceful error handling.

    Converts low-level errors (JSONDecodeError, ValidationError) into
    user-friendly CorruptedStateError exceptions.

    Args:
        path: Path to state file
        model_class: Pydantic model class for deserialization
        state_type: Human-readable name for error messages

    Returns:
        Loaded state document

    Raises:
        FileNotFoundError: If file doesn't exist
        CorruptedStateError: If file is corrupted or has invalid schema
    """
    try:
        return load_generic_state(path, model_class)
    except json.JSONDecodeError as e:
        raise CorruptedStateError(
            path,
            state_type,
            f"Invalid JSON format (line {e.lineno}, column {e.colno})",
        )
    except ValidationError as e:
        error_count = len(e.errors())
        first_error = e.errors()[0]
        field = ".".join(str(loc) for loc in first_error["loc"])
        raise CorruptedStateError(
            path,
            state_type,
            f"Schema validation failed: {field} - {first_error['msg']} ({error_count} error(s) total)",
        )
