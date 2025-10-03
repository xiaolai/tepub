from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from config.models import AppSettings, _NON_SLUG_CHARS, _WORD_SPLIT_PATTERN, _WORKSPACE_HASH_LENGTH
from exceptions import StateFileNotFoundError, WorkspaceNotFoundError


def derive_book_workspace(settings: AppSettings, input_epub: Path) -> Path:
    """
    Derive working directory alongside EPUB file.

    Example:
      /path/to/book.epub -> /path/to/book/
    """
    epub_path = input_epub.expanduser().resolve()
    return epub_path.parent / epub_path.stem


def with_book_workspace(settings: AppSettings, input_epub: Path) -> AppSettings:
    """Create settings with book-specific workspace, loading per-book config if exists."""
    from config.loader import _parse_yaml_file

    derived = derive_book_workspace(settings, input_epub)
    new_settings = settings.model_copy(update={"work_root": derived.parent, "work_dir": derived})

    # Load per-book config if it exists
    book_config = derived / "config.yaml"
    if book_config.exists():
        book_payload = _parse_yaml_file(book_config)
        if book_payload and isinstance(book_payload, dict):
            new_settings = new_settings.model_copy(update=book_payload)

    return new_settings


def with_override_root(settings: AppSettings, base_path: Path, input_epub: Path) -> AppSettings:
    """Override work directory with explicit path."""
    base_path = base_path.expanduser()
    if not base_path.is_absolute():
        base_path = Path.cwd() / base_path

    # If path looks like a working directory, use it directly
    segments_exists = (base_path / "segments.json").exists()
    state_exists = (base_path / "state.json").exists()
    if segments_exists or state_exists:
        return settings.model_copy(update={"work_root": base_path.parent, "work_dir": base_path})

    # Otherwise use it as root and create book-specific subdir
    work_dir = base_path / build_workspace_name(input_epub)
    return settings.model_copy(update={"work_root": base_path, "work_dir": work_dir})


def validate_for_export(settings: AppSettings, input_epub: Path) -> None:
    """Validate that all required files exist for export operations.

    Args:
        settings: AppSettings instance
        input_epub: Path to the EPUB file being processed

    Raises:
        WorkspaceNotFoundError: If workspace directory doesn't exist
        StateFileNotFoundError: If required state files are missing
        CorruptedStateError: If state files are corrupted
    """
    from state.base import safe_load_state
    from state.models import SegmentsDocument, StateDocument

    if not settings.work_dir.exists():
        raise WorkspaceNotFoundError(input_epub, settings.work_dir)

    if not settings.segments_file.exists():
        raise StateFileNotFoundError("segments", input_epub)

    if not settings.state_file.exists():
        raise StateFileNotFoundError("translation", input_epub)

    # Validate that files can actually be loaded (not corrupted)
    safe_load_state(settings.segments_file, SegmentsDocument, "segments")
    safe_load_state(settings.state_file, StateDocument, "translation")


def validate_for_translation(settings: AppSettings, input_epub: Path) -> None:
    """Validate that required files exist for translation operations.

    Args:
        settings: AppSettings instance
        input_epub: Path to the EPUB file being processed

    Raises:
        WorkspaceNotFoundError: If workspace directory doesn't exist
        StateFileNotFoundError: If segments file is missing
        CorruptedStateError: If segments file is corrupted
    """
    from state.base import safe_load_state
    from state.models import SegmentsDocument

    if not settings.work_dir.exists():
        raise WorkspaceNotFoundError(input_epub, settings.work_dir)

    if not settings.segments_file.exists():
        raise StateFileNotFoundError("segments", input_epub)

    # Validate that segments file can actually be loaded (not corrupted)
    safe_load_state(settings.segments_file, SegmentsDocument, "segments")


def build_workspace_name(input_epub: Path) -> str:
    """Build workspace directory name from EPUB filename."""
    first_word = _extract_first_word(input_epub)
    digest = hashlib.sha1(
        str(input_epub.expanduser().resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:_WORKSPACE_HASH_LENGTH]
    return f"{first_word}-{digest}" if digest else first_word


def _extract_first_word(input_epub: Path) -> str:
    """Extract first word from EPUB filename for workspace naming."""
    stem = input_epub.stem
    tokens = [token for token in _WORD_SPLIT_PATTERN.split(stem) if token]
    candidate = tokens[0] if tokens else "book"
    normalized = unicodedata.normalize("NFKD", candidate)
    ascii_candidate = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_candidate = _NON_SLUG_CHARS.sub("", ascii_candidate)
    return ascii_candidate or "book"


# Attach workspace methods to AppSettings for backward compatibility
AppSettings.derive_book_workspace = derive_book_workspace  # type: ignore
AppSettings.with_book_workspace = with_book_workspace  # type: ignore
AppSettings.with_override_root = with_override_root  # type: ignore
AppSettings.validate_for_export = validate_for_export  # type: ignore
AppSettings.validate_for_translation = validate_for_translation  # type: ignore
