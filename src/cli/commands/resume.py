"""Resume command implementation."""

import click
from rich.table import Table

from config import AppSettings
from console_singleton import get_console
from state.resume import load_resume_info

console = get_console()


@click.command()
@click.pass_context
def resume(ctx: click.Context) -> None:
    """Show resumable state summary."""

    settings: AppSettings = ctx.obj["settings"]
    info = load_resume_info(settings.state_file)
    table = Table(title="Translation Resume Info")
    table.add_column("Category")
    table.add_column("Count")
    table.add_row("Remaining", str(len(info.remaining_segments)))
    table.add_row("Completed", str(len(info.completed_segments)))
    table.add_row("Skipped", str(len(info.skipped_segments)))
    console.print(table)
