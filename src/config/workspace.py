from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from config.models import _NON_SLUG_CHARS, _WORD_SPLIT_PATTERN, _WORKSPACE_HASH_LENGTH, AppSettings
from exceptions import StateFileNotFoundError, WorkspaceNotFoundError
from logging_utils.logger import get_logger

logger = get_logger(__name__)


def derive_book_workspace(settings: AppSettings, input_epub: Path) -> Path:
    """
    Derive working directory alongside EPUB file.

    Example:
      /path/to/book.epub -> /path/to/book/
    """
    epub_path = input_epub.expanduser().resolve()
    return epub_path.parent / epub_path.stem


def _with_book_config(settings: AppSettings) -> AppSettings:
    """The settings with the workspace's own config.yaml applied, when it has one.

    Applied wherever the workspace is: under --work-dir it was never read, and
    a book's file list, prompt and output mode were silently ignored.
    """
    import pydantic

    from config.loader import (
        ConfigFileError,
        _parse_yaml_file,
        _prepare_provider_credentials,
        yaml,
    )
    from config.provenance import BOOK, ConfigLayer
    from translation.prompt_builder import configure_prompt

    book_config = settings.work_dir / "config.yaml"
    # A file holding a list or a lone value is refused by the parser; it used
    # to be skipped, and the book ran on settings it did not ask for.
    # Errors become ConfigFileError, which the entry point reports in one
    # line: malformed YAML or a bad value here escaped as a traceback.
    try:
        book_payload = _parse_yaml_file(book_config)
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"{book_config} is not valid YAML: {exc}") from exc
    # Recorded with what the loader read, replacing a book layer an earlier
    # workspace choice added, so `config show` can name where each value came
    # from. The path is the one read, before the book's config can move work_dir.
    layers = (
        *(layer for layer in settings.config_layers if layer.label != BOOK),
        ConfigLayer(BOOK, book_config, book_config.exists(), book_payload),
    )
    updated = settings
    if book_payload:
        unknown = sorted(set(book_payload) - set(AppSettings.model_fields))
        if unknown:
            logger.warning(
                "Ignoring settings tepub does not use in %s: %s", book_config, ", ".join(unknown)
            )
        try:
            updated = settings.model_copy(update=book_payload)
        except pydantic.ValidationError as exc:
            raise ConfigFileError(f"{book_config} has invalid settings:\n{exc}") from exc
        if "skip_rules" in book_payload:
            # A book's rules add to the ones it inherits, as its config.yaml
            # says; they used to replace them, so adding "prologue" dropped
            # "copyright", "index" and the rest.
            # Keywords are normalised, so each is kept once, the book's own
            # repeats included.
            seen = {rule.keyword for rule in settings.skip_rules}
            added = []
            for rule in updated.skip_rules:
                if rule.keyword not in seen:
                    seen.add(rule.keyword)
                    added.append(rule)
            updated = updated.model_copy(update={"skip_rules": [*settings.skip_rules, *added]})
        if "primary_provider" in book_payload:
            # The main loader fills a provider's key and address from the
            # environment; a book's own provider lost OLLAMA_BASE_URL without
            # it. The book's config sits above the environment, so a key or
            # address the book sets itself is kept: re-applying the variable
            # over it replaced the book's explicit base_url.
            book_fields = updated.primary_provider.model_fields_set
            updated = _prepare_provider_credentials(updated, keep=frozenset(book_fields))
    # The prompt builder holds global state. Configured only when a book set a
    # prompt, the next book in the same process kept that prompt.
    configure_prompt(updated.prompt_preamble)
    if updated is settings:
        updated = settings.model_copy()
    updated.config_layers = layers
    return updated


def with_book_workspace(settings: AppSettings, input_epub: Path) -> AppSettings:
    """Create settings with book-specific workspace, loading per-book config if exists."""
    derived = derive_book_workspace(settings, input_epub)
    return _with_book_config(
        settings.model_copy(update={"work_root": derived.parent, "work_dir": derived})
    )


def with_override_root(settings: AppSettings, base_path: Path, input_epub: Path) -> AppSettings:
    """Override work directory with explicit path."""
    base_path = base_path.expanduser()
    if not base_path.is_absolute():
        base_path = Path.cwd() / base_path

    # If path looks like a working directory, use it directly
    segments_exists = (base_path / "segments.json").exists()
    state_exists = (base_path / "state.json").exists()
    if segments_exists or state_exists:
        return _with_book_config(
            settings.model_copy(update={"work_root": base_path.parent, "work_dir": base_path})
        )

    # Otherwise use it as root and create book-specific subdir
    work_dir = base_path / build_workspace_name(input_epub)
    return _with_book_config(
        settings.model_copy(update={"work_root": base_path, "work_dir": work_dir})
    )


def epub_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_same_book(segments_doc, input_epub: Path) -> None:
    """Fail when segments.json was extracted from a different book.

    The book is identified by its content when extraction recorded a digest, so
    a moved or renamed EPUB is still the same book; comparing paths alone made
    translate and export refuse it with no way to override. Workspaces made
    before the digest was recorded fall back to comparing paths.
    """
    from exceptions import ArtifactMismatchError

    recorded = Path(str(segments_doc.epub_path))
    if segments_doc.epub_sha256:
        if epub_digest(input_epub) == segments_doc.epub_sha256:
            return
        raise ArtifactMismatchError(input_epub, recorded)
    try:
        same = recorded.resolve() == input_epub.resolve()
    except OSError:
        same = recorded == input_epub
    if not same:
        raise ArtifactMismatchError(input_epub, recorded)


def _check_workspace(settings: AppSettings, input_epub: Path, *, with_state: bool) -> None:
    """The workspace exists, its files load, and its segments are this book's."""
    from state.base import safe_load_state
    from state.models import SegmentsDocument, StateDocument

    if not settings.work_dir.exists():
        raise WorkspaceNotFoundError(input_epub, settings.work_dir)
    if not settings.segments_file.exists():
        raise StateFileNotFoundError("segments", input_epub)
    if with_state and not settings.state_file.exists():
        raise StateFileNotFoundError("translation", input_epub)

    # Validate that files can actually be loaded (not corrupted)
    segments_doc = safe_load_state(settings.segments_file, SegmentsDocument, "segments")
    if with_state:
        safe_load_state(settings.state_file, StateDocument, "translation")
    assert_same_book(segments_doc, input_epub)


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
    _check_workspace(settings, input_epub, with_state=True)


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
    _check_workspace(settings, input_epub, with_state=False)


def build_workspace_name(input_epub: Path) -> str:
    """Build workspace directory name from EPUB filename."""
    first_word = _extract_first_word(input_epub)
    digest = hashlib.sha1(
        str(input_epub.expanduser().resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:_WORKSPACE_HASH_LENGTH]
    return f"{first_word}-{digest}"


def _extract_first_word(input_epub: Path) -> str:
    """Extract first word from EPUB filename for workspace naming."""
    stem = input_epub.stem
    tokens = [token for token in _WORD_SPLIT_PATTERN.split(stem) if token]
    candidate = tokens[0] if tokens else "book"
    normalized = unicodedata.normalize("NFKD", candidate)
    ascii_candidate = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_candidate = _NON_SLUG_CHARS.sub("", ascii_candidate)
    return ascii_candidate or "book"

