"""Generic state management base for translation and audiobook state.

This module provides type-safe, reusable state operations that can be
shared across different state management systems in the application.
"""

from __future__ import annotations

import json
from collections.abc import Callable
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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(payload, indent=2, ensure_ascii=False)

    if HAS_PORTALOCKER:
        # Use file locking for concurrent write protection
        with portalocker.Lock(tmp_path, "w", encoding="utf-8", timeout=30) as f:
            f.write(content)
    else:
        # Fallback without locking (better than nothing)
        tmp_path.write_text(content, encoding="utf-8")

    tmp_path.replace(path)


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
