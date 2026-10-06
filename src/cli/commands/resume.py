"""resume: the old name of status, kept for a release."""

from pathlib import Path

import click

from cli.commands.status import show_status
from cli.core import optional_book
from console_singleton import get_console


@click.command(hidden=True)
@optional_book
@click.pass_context
def resume(ctx: click.Context, book: Path | None) -> None:
    """Deprecated: use `tepub status`."""
    get_console().print(
        "[yellow]resume is deprecated; use `tepub status`. "
        "It will be removed in a later release.[/yellow]"
    )
    show_status(ctx, book)
