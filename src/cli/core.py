"""Shared CLI utilities and common operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NoReturn

import click
from rich.markup import escape

from config import AppSettings, load_settings_from_cli
from exceptions import AmbiguousWorkspaceError, TepubError
from logging_utils.logger import configure_logging
from state.base import safe_load_state
from state.models import SegmentsDocument, StateDocument


def prepare_initial_settings(
    config_file: str | None, work_dir: Path | None, verbose: bool
) -> AppSettings:
    """Initialize settings from CLI arguments.

    Args:
        config_file: Optional path to config file
        work_dir: Optional work directory override
        verbose: Enable verbose logging

    Returns:
        Configured AppSettings instance
    """
    configure_logging()
    if verbose:
        configure_logging(level=logging.DEBUG)
    settings = load_settings_from_cli(config_file)
    if work_dir:
        settings = settings.model_copy(update={"work_dir": work_dir})
    # Note: ensure_directories() is called later in prepare_settings_for_epub()
    # after the workspace is properly configured
    return settings


def prepare_settings_for_epub(
    ctx: click.Context,
    settings: AppSettings,
    input_epub: Path,
    override: Path | None,
    *,
    create: bool = True,
) -> AppSettings:
    """Prepare settings for a specific EPUB file.

    Args:
        ctx: Click context
        settings: Base settings
        input_epub: Path to EPUB file
        override: Optional work directory override
        create: Create the workspace's directories. Commands that only read an
            existing workspace pass False: creating it left empty folders
            behind, and failed on read-only storage before saying anything.

    Returns:
        Settings configured for the specific EPUB
    """
    base_override = override or ctx.obj.get("work_dir_override_path")

    if base_override:
        settings = settings.with_override_root(base_override, input_epub)
        ctx.obj["work_dir_overridden"] = True
        ctx.obj["work_dir_override_path"] = base_override
    elif not ctx.obj.get("work_dir_overridden", False):
        settings = settings.with_book_workspace(input_epub)

    if create:
        settings.ensure_directories()
    ctx.obj["settings"] = settings
    return settings


def resolve_bookless_workspace(settings: AppSettings) -> AppSettings:
    """The workspace for a command that takes no book, such as resume or format.

    translate and extract put each book in its own folder under --work-dir,
    while commands without a book read --work-dir itself, so they reported no
    state for a book that was half translated. A folder that is a workspace is
    used as it is; a single book workspace inside it is used; several are
    listed rather than guessed. The chosen workspace's config.yaml applies, as
    it does when the book is given.
    """
    from config.workspace import _with_book_config

    root = settings.work_dir
    # The artifact names as configured, relative to a workspace; fixed names
    # missed renamed artifacts and took an artifact subfolder for a workspace.
    names = [
        path.relative_to(root) if path.is_relative_to(root) else Path(path.name)
        for path in (settings.segments_file, settings.state_file)
    ]

    def is_workspace(folder: Path) -> bool:
        return any((folder / name).exists() for name in names)

    if is_workspace(root):
        return _with_book_config(settings)
    if not root.is_dir():
        return settings
    candidates = sorted(child for child in root.iterdir() if child.is_dir() and is_workspace(child))
    if len(candidates) == 1:
        return _with_book_config(
            settings.model_copy(update={"work_root": root, "work_dir": candidates[0]})
        )
    if len(candidates) > 1:
        raise AmbiguousWorkspaceError(root, candidates)
    return settings


def bookless_settings(ctx: click.Context) -> AppSettings:
    """Settings for a command without a book, its workspace resolved.

    Several matching workspaces end the command with a message listing them.
    """
    from console_singleton import get_console

    try:
        settings = resolve_bookless_workspace(ctx.obj["settings"])
    except AmbiguousWorkspaceError as exc:
        # Escaped: a folder named like "[/blue]" ended in a MarkupError.
        get_console().print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1) from exc
    except OSError as exc:
        _unreadable_workspace(ctx.obj["settings"].work_dir, exc)
    ctx.obj["settings"] = settings
    return settings


def describe_pipeline_artifacts(settings: AppSettings, input_epub: Path) -> tuple[bool, str]:
    """Report whether existing artifacts are reusable, and why not when they aren't.

    The boolean-only check collapsed missing, unreadable, wrong-EPUB and
    incomplete artifacts into a single False, so the pipeline silently re-ran a
    full extraction with no indication of which condition applied.

    Unreadable or corrupt artifacts raise: extraction reads them too, so
    re-extracting could not repair them, only fail later with a traceback.
    """
    segments_path = settings.segments_file
    state_path = settings.state_file

    if not segments_path.exists():
        return False, f"no segments file at {segments_path}"
    if not state_path.exists():
        return False, f"no translation state at {state_path}"

    segments_doc = safe_load_state(segments_path, SegmentsDocument, "segments")
    state_doc = safe_load_state(state_path, StateDocument, "translation")

    # The book is identified by its content, as translate and export do: a
    # moved or renamed book was re-extracted when its path was compared.
    from config.workspace import assert_same_book
    from exceptions import ArtifactMismatchError

    try:
        assert_same_book(segments_doc, input_epub)
    except ArtifactMismatchError:
        return False, f"artifacts belong to a different EPUB ({segments_doc.epub_path})"

    # Older segmentation is updated by extraction, which carries translations
    # over; resumed as it was, its units no longer match the book on export.
    from extraction.migrate import SEGMENTS_FORMAT

    if segments_doc.format_version < SEGMENTS_FORMAT:
        return False, f"segments use an older format ({segments_doc.format_version})"

    # Validate segments match state.
    # Requiring *every* extracted segment id to appear in state was too strict:
    # translation filtering and skip rules legitimately leave some segments out,
    # so valid workspaces were reported invalid and re-extracted. A shared subset
    # is enough to prove the two artifacts came from the same extraction, while
    # no overlap at all still catches genuinely mismatched files.
    if not segments_doc.segments:
        return False, "segments file contains no segments"
    records = state_doc.segments
    shared = [segment for segment in segments_doc.segments if segment.segment_id in records]
    if not shared:
        return False, "segments and translation state share no segment ids"

    # Unit ids come from a unit's place, not its text, so another book's state
    # can share them; the epub digest vouches for segments.json only. Each
    # record carries the digest of the source it was extracted with, so the
    # state must match these units' text. Re-extraction stamps records that
    # lack one and resets those that differ, which reuse would skip.
    from extraction.pipeline import source_digest

    unstamped = sum(1 for segment in shared if records[segment.segment_id].source_sha256 is None)
    if unstamped:
        return False, f"{unstamped} translation records have no source digest yet"
    changed = sum(
        1
        for segment in shared
        if records[segment.segment_id].source_sha256 != source_digest(segment)
    )
    if changed:
        return False, f"{changed} translation records were made from other source text"

    return True, "reusable"


def translation_options(func):
    """--from, --to, --provider, --model and --allow-failures, shared by
    translate and pipeline."""
    from translation.providers import PROVIDER_NAMES

    options = [
        click.option(
            "--from", "source_language", default=None, help="Source language (code or name)."
        ),
        click.option(
            "--to", "target_language", default=None, help="Target language (code or name)."
        ),
        click.option(
            "--provider",
            type=click.Choice(PROVIDER_NAMES, case_sensitive=False),
            help="Translation provider for this run, instead of the configured one.",
        ),
        click.option("--model", help="Model for this run, instead of the configured one."),
        click.option(
            "--allow-failures",
            is_flag=True,
            help="Exit with 0 even when some units failed (otherwise 3).",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def with_provider(settings: AppSettings, provider: str | None, model: str | None) -> AppSettings:
    """Settings using this run's provider and model, when given.

    Comparing two models meant editing the config between runs. Another
    provider needs its model too: model names do not carry across providers.
    """
    from config import ProviderConfig
    from config.loader import _prepare_provider_credentials

    if provider is None and model is None:
        return settings
    current = settings.primary_provider
    if provider is None or provider.lower() == current.name:
        config = current.model_copy(update={"model": model or current.model})
    elif model is None:
        raise click.UsageError(
            f"--provider {provider} needs --model as well; the configured model "
            f"{current.model!r} belongs to {current.name}."
        )
    else:
        config = ProviderConfig(name=provider, model=model)
    # The environment's keys and server, such as OLLAMA_BASE_URL, were applied
    # to the configured provider only, so another one chosen here lost them.
    return _prepare_provider_credentials(settings.model_copy(update={"primary_provider": config}))


def with_languages(
    settings: AppSettings, source: str | None, target: str | None
) -> tuple[AppSettings, str, str]:
    """Settings using this run's --from and --to, when given, and the two
    languages' codes; shared by translate and pipeline, which had drifted."""
    from translation.languages import normalize_language

    source = source or settings.source_language
    target = target or settings.target_language
    settings = settings.model_copy(update={"source_language": source, "target_language": target})
    return settings, normalize_language(source)[0], normalize_language(target)[0]


# Exit code for a run that finished with units it could not translate, or
# could not insert into the exported book.
EXIT_UNITS_FAILED = 3


def settings_for_book(ctx: click.Context, book: Path | None) -> AppSettings:
    """Settings for a command that takes the book optionally.

    resume, format and several debug commands took no book and looked for a
    workspace in the current folder or --work-dir, unlike every other
    command; given the book, they use its workspace as the others do.

    An existing workspace must belong to the book: --work-dir naming another
    book's workspace let format and purge-refusals change that book's state.
    These commands work on an existing workspace, so none is created.
    """
    if book is None:
        return bookless_settings(ctx)
    base: AppSettings = ctx.obj["settings"]
    try:
        settings = prepare_settings_for_epub(ctx, base, book, override=None, create=False)
        has_segments = settings.segments_file.exists()
        has_state = settings.state_file.exists()
    except OSError as exc:
        _unreadable_workspace(base.work_dir, exc)
    if has_segments:
        from config.workspace import assert_same_book

        try:
            segments_doc = safe_load_state(settings.segments_file, SegmentsDocument, "segments")
        except OSError as exc:
            _unreadable_workspace(settings.work_dir, exc)
        try:
            assert_same_book(segments_doc, book)
        except OSError as exc:
            # Hashing the book to confirm it is the workspace's.
            _unreadable(f"The book {book}", exc)
    elif has_state:
        # Only segments.json records the book; a state without it could be any
        # book's, and was formatted or purged as this one's.
        raise TepubError(
            f"{settings.work_dir} has translation state but no segments file, so it "
            f"cannot be confirmed to belong to {book.name}.\n"
            "Extract the book into it again with tepub extract, which keeps the "
            "translations of an unchanged book."
        )
    return settings


def _unreadable_workspace(path: Path, exc: OSError) -> NoReturn:
    """End the command on a workspace that cannot be read, such as one without
    permission: resolving it raised a traceback."""
    _unreadable(f"The workspace at {path}", exc)


def _unreadable(what: str, exc: OSError) -> NoReturn:
    """End the command on a file that cannot be read, naming it and the error."""
    from console_singleton import get_console

    get_console().print(f"[red]{escape(what)} could not be read: {escape(str(exc))}[/red]")
    raise SystemExit(1) from exc


def optional_book(func):
    """An optional BOOK argument for commands that also work without one."""
    return click.argument(
        "book", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )(func)
