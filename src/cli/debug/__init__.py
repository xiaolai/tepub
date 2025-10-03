"""Debug command group and registration."""

import click

from .commands import (
    analyze_skips,
    inspect_segment_cmd,
    list_files_cmd,
    preview_skips,
    purge_refusals,
    show_pending_cmd,
    show_skip_list_cmd,
    workspace,
)


@click.group()
@click.pass_context
def debug(ctx: click.Context) -> None:  # pragma: no cover - primarily used interactively
    """Debugging utilities for inspecting pipeline state."""
    pass


def register_debug_commands(app: click.Group) -> None:
    """Register debug group with all debug commands."""
    # Add all debug subcommands
    debug.add_command(show_skip_list_cmd)
    debug.add_command(show_pending_cmd)
    debug.add_command(purge_refusals)
    debug.add_command(inspect_segment_cmd)
    debug.add_command(list_files_cmd)
    debug.add_command(preview_skips)
    debug.add_command(workspace)
    debug.add_command(analyze_skips)

    # Register debug group to main app
    app.add_command(debug)


__all__ = ["debug", "register_debug_commands"]
