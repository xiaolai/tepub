"""Debug command implementations."""

from __future__ import annotations

from pathlib import Path

import click

from cli.core import prepare_settings_for_epub
from config import AppSettings
from console_singleton import get_console
from state.models import SegmentStatus, TranslationRecord
from state.store import load_state, save_state
from translation.refusal_filter import looks_like_refusal

console = get_console()


@click.command("show-skip-list")
@click.pass_context
def show_skip_list_cmd(ctx: click.Context) -> None:
    """Show configured skip rules."""
    from debug_tools.skip_lists import show_skip_list

    settings: AppSettings = ctx.obj["settings"]
    show_skip_list(settings)


@click.command("show-pending")
@click.pass_context
def show_pending_cmd(ctx: click.Context) -> None:
    """Show pending segments."""
    from debug_tools.pending import show_pending

    settings: AppSettings = ctx.obj["settings"]
    show_pending(settings)


@click.command("purge-refusals")
@click.option("--dry-run", is_flag=True, help="Only report matches without modifying state.")
@click.pass_context
def purge_refusals(ctx: click.Context, dry_run: bool) -> None:
    """Reset segments whose translations look like provider refusals."""

    settings: AppSettings = ctx.obj["settings"]

    try:
        state = load_state(settings.state_file)
    except FileNotFoundError:
        console.print("[red]State file not found. Run extract/translate first.[/red]")
        return

    matches: list[str] = []

    for segment_id, record in state.segments.items():
        if record.translation and looks_like_refusal(record.translation):
            matches.append(segment_id)
            if dry_run:
                continue
            payload = record.model_dump()
            payload.update(
                {
                    "translation": None,
                    "status": SegmentStatus.PENDING,
                    "provider_name": None,
                    "model_name": None,
                    "error_message": None,
                }
            )
            state.segments[segment_id] = TranslationRecord.model_validate(payload)

    if not matches:
        console.print("[green]No refusal-like translations found.[/green]")
        return

    if dry_run:
        console.print(f"[yellow]Found {len(matches)} refusal-like segments (dry run).[/yellow]")
        for segment_id in matches:
            console.print(f" - {segment_id}")
        return

    save_state(state, settings.state_file)
    console.print(
        f"[green]Reset {len(matches)} segments to pending; rerun translate to retry them.[/green]"
    )


@click.command("inspect-segment")
@click.argument("segment_id")
@click.pass_context
def inspect_segment_cmd(ctx: click.Context, segment_id: str) -> None:
    """Inspect a specific segment."""
    from debug_tools.inspect import inspect_segment

    settings: AppSettings = ctx.obj["settings"]
    inspect_segment(settings, segment_id)


@click.command("list-files")
@click.pass_context
def list_files_cmd(ctx: click.Context) -> None:
    """List all processed files."""
    from debug_tools.files import list_files

    settings: AppSettings = ctx.obj["settings"]
    list_files(settings)


@click.command("preview-skip-candidates")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def preview_skips(ctx: click.Context, input_epub: Path) -> None:
    """Preview skip candidates for an EPUB."""
    from debug_tools.preview import preview_skip_candidates

    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    preview_skip_candidates(settings, input_epub)


@click.command("workspace")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def workspace(ctx: click.Context, input_epub: Path) -> None:
    """Show workspace paths for an EPUB."""
    settings: AppSettings = ctx.obj["settings"]
    preview_settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    console.print(f"Base work root: {preview_settings.work_root}", soft_wrap=True)
    console.print(f"Derived workspace: {preview_settings.work_dir}", soft_wrap=True)
    console.print(f"Segments file: {preview_settings.segments_file}", soft_wrap=True)
    console.print(f"State file: {preview_settings.state_file}", soft_wrap=True)


@click.command("analyze-skips")
@click.option(
    "--library",
    type=click.Path(path_type=Path),
    help="Directory or EPUB to analyze (defaults to ~/Ultimate/epub).",
)
@click.option("--limit", type=int, help="Process at most N EPUB files.")
@click.option(
    "--top-n",
    type=int,
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
    library: Path | None,
    limit: int | None,
    top_n: int,
    report: Path | None,
) -> None:
    """Analyze skip rules across an EPUB library."""
    from debug_tools.analysis import analyze_library

    settings: AppSettings = ctx.obj["settings"]
    target_library = library or (Path.home() / "Ultimate" / "epub")
    analyze_library(settings, target_library, limit=limit, top_n=top_n, report_path=report)
