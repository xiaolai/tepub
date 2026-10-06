"""Shared CLI utilities and common operations."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from config import AppSettings, load_settings_from_cli
from exceptions import AmbiguousWorkspaceError
from logging_utils.logger import configure_logging
from state.store import load_segments, load_state


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
    ctx: click.Context, settings: AppSettings, input_epub: Path, override: Path | None
) -> AppSettings:
    """Prepare settings for a specific EPUB file.

    Args:
        ctx: Click context
        settings: Base settings
        input_epub: Path to EPUB file
        override: Optional work directory override

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

    settings.ensure_directories()
    ctx.obj["settings"] = settings
    return settings


def resolve_bookless_workspace(settings: AppSettings) -> AppSettings:
    """The workspace for a command that takes no book, such as resume or format.

    translate and extract put each book in its own folder under --work-dir,
    while commands without a book read --work-dir itself, so they reported no
    state for a book that was half translated. A folder that is a workspace is
    used as it is; a single book workspace inside it is used; several are
    listed rather than guessed.
    """
    root = settings.work_dir
    if (root / "segments.json").exists() or (root / "state.json").exists():
        return settings
    if not root.is_dir():
        return settings
    candidates = sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and ((child / "segments.json").exists() or (child / "state.json").exists())
    )
    if len(candidates) == 1:
        return settings.model_copy(update={"work_root": root, "work_dir": candidates[0]})
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
        get_console().print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    ctx.obj["settings"] = settings
    return settings


def check_pipeline_artifacts(settings: AppSettings, input_epub: Path) -> bool:
    """Check if valid pipeline artifacts exist for resuming.

    Args:
        settings: Application settings
        input_epub: EPUB file path

    Returns:
        True if valid artifacts exist, False otherwise
    """
    return describe_pipeline_artifacts(settings, input_epub)[0]


def describe_pipeline_artifacts(settings: AppSettings, input_epub: Path) -> tuple[bool, str]:
    """Report whether existing artifacts are reusable, and why not when they aren't.

    The boolean-only check collapsed missing, unreadable, wrong-EPUB and
    incomplete artifacts into a single False, so the pipeline silently re-ran a
    full extraction with no indication of which condition applied.
    """
    segments_path = settings.segments_file
    state_path = settings.state_file

    if not segments_path.exists():
        return False, f"no segments file at {segments_path}"
    if not state_path.exists():
        return False, f"no translation state at {state_path}"

    try:
        segments_doc = load_segments(segments_path)
        state_doc = load_state(state_path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # A bare `except Exception` also swallowed programming defects and
        # permission errors, silently triggering a full re-extraction instead of
        # surfacing the real problem.
        return False, f"artifacts could not be read ({exc})"

    # The book is identified by its content, as translate and export do: a
    # moved or renamed book was re-extracted when its path was compared.
    from config.workspace import assert_same_book
    from exceptions import ArtifactMismatchError

    try:
        assert_same_book(segments_doc, input_epub)
    except ArtifactMismatchError:
        return False, f"artifacts belong to a different EPUB ({segments_doc.epub_path})"

    # Validate segments match state.
    # Requiring *every* extracted segment id to appear in state was too strict:
    # translation filtering and skip rules legitimately leave some segments out,
    # so valid workspaces were reported invalid and re-extracted. A shared subset
    # is enough to prove the two artifacts came from the same extraction, while
    # no overlap at all still catches genuinely mismatched files.
    segment_ids = {segment.segment_id for segment in segments_doc.segments}
    if not segment_ids:
        return False, "segments file contains no segments"

    if not segment_ids & state_doc.segments.keys():
        return False, "segments and translation state share no segment ids"

    return True, "reusable"


def translation_options(func):
    """--from, --to, --provider, --model and --allow-failures, shared by
    translate and pipeline."""
    from translation.providers import PROVIDER_NAMES

    options = [
        click.option("--from", "source_language", default=None, help="Source language (code or name)."),
        click.option("--to", "target_language", default=None, help="Target language (code or name)."),
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
    return settings.model_copy(update={"primary_provider": config})


# Exit code for a run that finished with units it could not translate.
EXIT_UNITS_FAILED = 3


def settings_for_book(ctx: click.Context, book: Path | None) -> AppSettings:
    """Settings for a command that takes the book optionally.

    resume, format and several debug commands took no book and looked for a
    workspace in the current folder or --work-dir, unlike every other
    command; given the book, they use its workspace as the others do.
    """
    if book is None:
        return bookless_settings(ctx)
    return prepare_settings_for_epub(ctx, ctx.obj["settings"], book, override=None)


def optional_book(func):
    """An optional BOOK argument for commands that also work without one."""
    return click.argument(
        "book", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )(func)
