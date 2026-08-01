"""Tepub CLI main entry point."""

from __future__ import annotations

from pathlib import Path

import click

from cli.commands import register_commands
from cli.core import prepare_initial_settings
from cli.debug import register_debug_commands
from console_singleton import configure_console, get_console

console = get_console()


class DefaultCommandGroup(click.Group):
    """Click group that supports a default command."""

    def __init__(self, *args, default_command: str | None = None, **kwargs):
        self.default_command = default_command
        super().__init__(*args, **kwargs)

    def _first_argument_index(self, args: list[str]) -> int | None:
        """Index of the first non-option token, skipping group options and values."""
        value_opts: set[str] = set()
        for param in self.params:
            if getattr(param, "is_flag", False):
                continue
            value_opts.update(param.opts)
            value_opts.update(param.secondary_opts)

        index = 0
        while index < len(args):
            token = args[index]
            if token == "--":
                return index + 1 if index + 1 < len(args) else None
            if token.startswith("-"):
                # "--opt=value" carries its value inline; "--opt value" consumes the
                # next token as well.
                index += 1 if "=" in token or token not in value_opts else 2
                continue
            return index
        return None

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if self.default_command and args:
            # Only args[0] was examined before, so any global option preceding an
            # implicit EPUB argument ("tepub --verbose book.epub") suppressed the
            # default command and the invocation failed to parse.
            index = self._first_argument_index(args)
            if index is not None and args[index] not in self.commands:
                args.insert(index, self.default_command)
        return super().parse_args(ctx, args)


@click.group(cls=DefaultCommandGroup, default_command="pipeline")
@click.option(
    "--config",
    "config_file",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config.yaml file.",
)
@click.option(
    "--work-dir",
    "work_dir",
    type=click.Path(path_type=Path),
    help="Override top-level work directory for all operations.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable verbose logging for debugging.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Suppress all console output.",
)
@click.pass_context
def app(
    ctx: click.Context,
    config_file: Path | None,
    work_dir: Path | None,
    verbose: bool,
    quiet: bool,
) -> None:
    """Tepub: EPUB Bilingual Translator & Multi-format Exporter."""
    configure_console(quiet=quiet, verbose=verbose)
    settings = prepare_initial_settings(config_file, work_dir, verbose)
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    if work_dir:
        # Record the override in the context too. prepare_settings_for_epub reads
        # it from here; storing it only on `settings` meant the per-book workspace
        # later overwrote work_dir and the --work-dir flag was silently ignored.
        ctx.obj["work_dir_override_path"] = work_dir
        ctx.obj["work_dir_overridden"] = True


# Register all commands
register_commands(app)
register_debug_commands(app)


if __name__ == "__main__":
    app()
