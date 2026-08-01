"""Resume command implementation."""

import click
from rich.table import Table

from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.resume import load_resume_info

console = get_console()


@click.command()
@click.pass_context
def resume(ctx: click.Context) -> None:
    """Show resumable state summary."""

    settings: AppSettings = ctx.obj["settings"]

    # A missing state file used to yield an all-zero table, which reads as
    # "nothing left to do" rather than "this workspace was never set up".
    if not settings.state_file.exists():
        console.print(f"[yellow]No translation state at {settings.state_file}.[/yellow]")
        console.print(
            "[dim]Run `tepub extract <book.epub>` first, or pass --work-dir to point at "
            "an existing workspace.[/dim]"
        )
        raise SystemExit(1)

    try:
        info = load_resume_info(settings.state_file)
    except (OSError, ValueError, TypeError, KeyError, CorruptedStateError) as exc:
        # Corrupted state previously escaped as a raw traceback.
        console.print(f"[red]Translation state at {settings.state_file} is unreadable: {exc}[/red]")
        raise SystemExit(1) from exc

    table = Table(title="Translation Resume Info")
    table.add_column("Category")
    table.add_column("Count")
    table.add_row("Remaining", str(len(info.remaining_segments)))
    table.add_row("Completed", str(len(info.completed_segments)))
    table.add_row("Skipped", str(len(info.skipped_segments)))
    console.print(table)
