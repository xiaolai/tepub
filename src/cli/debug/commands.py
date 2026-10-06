"""Debug command implementations."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import click
from rich.markup import escape

from cli.core import optional_book, prepare_settings_for_epub, settings_for_book
from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError, WorkspaceBusyError
from state.models import StateDocument
from state.store import load_state, reset_to_pending
from state.writer import update_state_atomic
from translation.refusal_filter import looks_like_refusal

console = get_console()


@click.command("show-skip-list")
@optional_book
@click.pass_context
def show_skip_list_cmd(ctx: click.Context, book: Path | None) -> None:
    """Show the documents and segments a book's extraction skipped."""
    from debug_tools.skip_lists import show_skip_list

    settings: AppSettings = settings_for_book(ctx, book)
    show_skip_list(settings)


@click.command("show-pending")
@optional_book
@click.pass_context
def show_pending_cmd(ctx: click.Context, book: Path | None) -> None:
    """Show pending segments."""
    from debug_tools.pending import show_pending

    settings: AppSettings = settings_for_book(ctx, book)
    show_pending(settings)


def _refusal_ids(state: StateDocument) -> list[str]:
    """The units whose translation reads like a provider's refusal."""
    return [
        segment_id
        for segment_id, record in state.segments.items()
        if record.translation and looks_like_refusal(record.translation)
    ]


def _reset_refusals(state_file: Path) -> list[str]:
    """Reset the refusals to pending under the state-file lock; the ids reset.

    Rescanned under the lock: an unlocked load-modify-save overwrote a
    concurrent translate run's work, and the count reported must be what was
    reset, not what an earlier read saw.
    """
    reset: list[str] = []

    def purge(state: StateDocument) -> StateDocument:
        reset[:] = _refusal_ids(state)
        reset_to_pending(state, reset)
        return state

    update_state_atomic(state_file, purge)
    return reset


def _state_failure(state_file: Path, exc: Exception, action: str) -> NoReturn:
    """End the command on a state file that is missing, unreadable or busy.

    A missing file is a failure: returning 0 made scripts treat it as a
    successful purge. Each of these used to end in a traceback somewhere.
    """
    path = escape(str(state_file))
    if isinstance(exc, FileNotFoundError):
        console.print("[red]State file not found. Run extract/translate first.[/red]")
    elif isinstance(exc, (WorkspaceBusyError, OSError)):
        console.print(f"[red]Could not {action} {escape(str(path))}: {escape(str(exc))}[/red]")
    else:
        console.print(
            f"[red]State file at {escape(str(path))} is unreadable: {escape(str(exc))}[/red]"
        )
    raise SystemExit(1) from exc


_STATE_ERRORS = (OSError, ValueError, TypeError, KeyError, CorruptedStateError, WorkspaceBusyError)


@click.command("purge-refusals")
@click.option("--dry-run", is_flag=True, help="Only report matches without modifying state.")
@optional_book
@click.pass_context
def purge_refusals(ctx: click.Context, dry_run: bool, book: Path | None) -> None:
    """Reset segments whose translations look like provider refusals."""

    settings: AppSettings = settings_for_book(ctx, book)
    try:
        matches = _refusal_ids(load_state(settings.state_file))
    except _STATE_ERRORS as exc:
        _state_failure(settings.state_file, exc, "read")

    if not matches:
        console.print("[green]No refusal-like translations found.[/green]")
        return
    if dry_run:
        console.print(f"[yellow]Found {len(matches)} refusal-like segments (dry run).[/yellow]")
        for segment_id in matches:
            console.print(f" - {escape(segment_id)}")
        return

    try:
        reset = _reset_refusals(settings.state_file)
    except _STATE_ERRORS as exc:
        _state_failure(settings.state_file, exc, "update")
    console.print(
        f"[green]Reset {len(reset)} segments to pending; rerun translate to retry them.[/green]"
    )


@click.command("inspect-segment")
@click.argument("segment_id")
@optional_book
@click.pass_context
def inspect_segment_cmd(ctx: click.Context, segment_id: str, book: Path | None) -> None:
    """Inspect a specific segment."""
    from debug_tools.inspect import inspect_segment

    settings: AppSettings = settings_for_book(ctx, book)
    inspect_segment(settings, segment_id)


@click.command("list-files")
@optional_book
@click.pass_context
def list_files_cmd(ctx: click.Context, book: Path | None) -> None:
    """List all processed files."""
    from debug_tools.files import list_files

    settings: AppSettings = settings_for_book(ctx, book)
    list_files(settings)


@click.command("preview-skip-candidates")
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def preview_skips(ctx: click.Context, input_epub: Path) -> None:
    """Preview skip candidates for an EPUB."""
    from debug_tools.preview import preview_skip_candidates

    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    preview_skip_candidates(settings, input_epub)


@click.command("workspace")
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def workspace(ctx: click.Context, input_epub: Path) -> None:
    """Show workspace paths for an EPUB."""
    settings: AppSettings = ctx.obj["settings"]
    preview_settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    console.print(f"Base work root: {escape(str(preview_settings.work_root))}", soft_wrap=True)
    console.print(f"Derived workspace: {escape(str(preview_settings.work_dir))}", soft_wrap=True)
    console.print(f"Segments file: {escape(str(preview_settings.segments_file))}", soft_wrap=True)
    console.print(f"State file: {escape(str(preview_settings.state_file))}", soft_wrap=True)


@click.command("analyze-skips")
@click.option(
    "--library",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Directory or EPUB to analyze.",
)
@click.option("--limit", type=click.IntRange(min=1), help="Process at most N EPUB files.")
@click.option(
    "--top-n",
    type=click.IntRange(min=0),
    default=15,
    show_default=True,
    help="Number of unmatched TOC titles to list.",
)
@click.option(
    "--report",
    type=click.Path(path_type=Path),
    help="Optional JSON file for detailed results.",
)
@click.pass_context
def analyze_skips(
    ctx: click.Context,
    library: Path,
    limit: int | None,
    top_n: int,
    report: Path | None,
) -> None:
    """Analyze skip rules across an EPUB library."""
    from debug_tools.analysis import analyze_library

    settings: AppSettings = ctx.obj["settings"]
    analyze_library(settings, library, limit=limit, top_n=top_n, report_path=report)
