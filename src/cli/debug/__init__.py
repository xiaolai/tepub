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

# Single inventory of debug subcommands. The names were previously listed twice —
# once in the import above and once as eight add_command calls — so adding a
# command meant editing two places, and forgetting the second failed silently.
DEBUG_COMMANDS = (
    show_skip_list_cmd,
    show_pending_cmd,
    purge_refusals,
    inspect_segment_cmd,
    list_files_cmd,
    preview_skips,
    workspace,
    analyze_skips,
)


@click.group()
def debug() -> None:  # pragma: no cover - primarily used interactively
    """Debugging utilities for inspecting pipeline state."""
    # No pass_context: the callback never used the injected ctx.
    pass


def register_debug_commands(app: click.Group) -> None:
    """Register debug group with all debug commands."""
    for command in DEBUG_COMMANDS:
        debug.add_command(command)

    # Register debug group to main app
    app.add_command(debug)


__all__ = ["DEBUG_COMMANDS", "debug", "register_debug_commands"]
