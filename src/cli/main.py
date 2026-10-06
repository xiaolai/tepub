"""Tepub CLI main entry point."""

from __future__ import annotations

import difflib
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

import click
import pydantic
import yaml
from rich.markup import escape

from cli.commands import register_commands
from cli.core import prepare_initial_settings
from cli.debug import register_debug_commands
from config.loader import ConfigFileError
from console_singleton import configure_console, get_console
from exceptions import TepubError


def _is_flag_cluster(token: str, short_flags: set[str]) -> bool:
    """Whether `token` is short global flags written together, as in -vq."""
    return (
        len(token) > 2
        and token[0] == "-"
        and token[1] != "-"
        and all(char in short_flags for char in token[1:])
    )


class DefaultCommandGroup(click.Group):
    """The tepub group: a book given alone runs the pipeline, global options
    are accepted after the command too, typos get a suggestion, and help lists
    the commands in the order a book goes through them."""

    # Shown first, in this order; any other command follows alphabetically.
    WORKFLOW = ("extract", "translate", "export", "pipeline", "status", "glossary")
    # Kept working for a release, but not listed.
    HIDDEN = frozenset({"resume"})

    def __init__(self, *args, default_command: str | None = None, **kwargs):
        self.default_command = default_command
        super().__init__(*args, **kwargs)

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = [name for name in super().list_commands(ctx) if name not in self.HIDDEN]
        first = [name for name in self.WORKFLOW if name in names]
        return first + sorted(name for name in names if name not in first)

    def _global_options(self) -> tuple[set[str], set[str]]:
        """(options taking a value, flags) of the group itself."""
        value_opts: set[str] = set()
        flags: set[str] = set()
        for param in self.params:
            names = flags if getattr(param, "is_flag", False) else value_opts
            names.update(param.opts)
            names.update(param.secondary_opts)
        return value_opts, flags

    def _command_value_options(self, name: str | None) -> dict[str, int]:
        """Options of the command `name`, and of its subcommands, that take
        values: each with the number of values it takes."""
        arity: dict[str, int] = {}
        stack = [self.commands.get(name)] if name else []
        while stack:
            command = stack.pop()
            if command is None:
                continue
            for param in command.params:
                if isinstance(param, click.Option) and not param.is_flag and not param.count:
                    arity.update(dict.fromkeys(param.opts, param.nargs))
            if isinstance(command, click.Group):
                stack.extend(command.commands.values())
        return arity

    def _first_argument_index(self, args: list[str]) -> int | None:
        """Index of the first non-option token, skipping group options and values."""
        value_opts = self._global_options()[0]
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

    def _hoist_global_options(self, args: list[str], start: int) -> list[str]:
        """Move global options found after the command to before it.

        `tepub resume --work-dir X` failed with "No such option": the global
        options were accepted only before the command. No command defines an
        option of the same name (a test holds that), so the move is unambiguous.
        """
        if "--" in args[:start]:
            # Everything after "--" is an argument, global-option look-alikes too.
            return args
        value_opts, flags = self._global_options()
        short_flags = {flag[1] for flag in flags if len(flag) == 2}
        # The command's own options keep their values: `--model -q` names a model.
        command = args[start] if args[start] in self.commands else self.default_command
        command_opts = self._command_value_options(command)
        front, rest = list(args[:start]), []
        index = start
        while index < len(args):
            token = args[index]
            if token == "--":
                rest.extend(args[index:])
                break
            name = token.split("=", 1)[0]
            if name in command_opts and "=" not in token:
                rest.extend(args[index : index + 1 + command_opts[name]])
                index += 1 + command_opts[name]
            elif name in flags or _is_flag_cluster(token, short_flags):
                front.append(token)
                index += 1
            elif name in value_opts and "=" in token:
                front.append(token)
                index += 1
            elif name in value_opts and index + 1 < len(args):
                front.extend(args[index : index + 2])
                index += 2
            else:
                rest.append(token)
                index += 1
        return front + rest

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        index = self._first_argument_index(args)
        if index is not None:
            args = self._hoist_global_options(args, index)
            index = self._first_argument_index(args)
        if self.default_command and index is not None and args[index] not in self.commands:
            # A book given alone runs the pipeline; anything else was taken for a
            # book too, so a typo read "Path 'transalte' does not exist".
            candidate = Path(args[index])
            if candidate.suffix.lower() == ".epub" and candidate.is_file():
                args.insert(index, self.default_command)
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx: click.Context, args: list[str]):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            name = args[0] if args else ""
            close = difflib.get_close_matches(name, self.list_commands(ctx), n=1)
            # Newer Click versions suggest by themselves; add one only where
            # it does not, or the message says it twice.
            if (
                close
                and "No such command" in exc.message
                and "Did you mean" not in exc.format_message()
            ):
                exc.message = f"No such command '{name}'. Did you mean '{close[0]}'?"
            raise


@click.group(cls=DefaultCommandGroup, default_command="pipeline")
@click.option(
    "--config",
    "config_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
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
@click.version_option(package_name="tepub", prog_name="tepub")
@click.pass_context
def app(
    ctx: click.Context,
    config_file: Path | None,
    work_dir: Path | None,
    verbose: bool,
    quiet: bool,
) -> None:
    """Translate EPUB books, and turn them into audiobooks and web pages.

    \b
    A book goes through three steps:
      tepub extract book.epub      find the text to translate
      tepub translate book.epub    translate it (resumable)
      tepub export book.epub       write the translated EPUB next to the book
    or all three at once:
      tepub book.epub

    \b
    tepub status book.epub shows how far a book has got.
    """
    configure_console(quiet=quiet)
    # Log records go to a console of their own, which --quiet does not reach;
    # errors still show. Set either way, as the setting is process-wide.
    logging.disable(logging.WARNING if quiet else logging.NOTSET)
    try:
        settings = prepare_initial_settings(config_file, work_dir, verbose)
    except (yaml.YAMLError, pydantic.ValidationError, ConfigFileError) as exc:
        raise click.ClickException(f"The configuration is not valid: {exc}") from exc
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    ctx.obj["config_file"] = config_file
    if work_dir:
        # Record the override in the context too. prepare_settings_for_epub reads
        # it from here; storing it only on `settings` meant the per-book workspace
        # later overwrote work_dir and the --work-dir flag was silently ignored.
        ctx.obj["work_dir_override_path"] = work_dir
        ctx.obj["work_dir_overridden"] = True


# Register all commands
register_commands(app)
register_debug_commands(app)


# The shell's convention for a process ended by Ctrl-C (128 + SIGINT).
EXIT_INTERRUPTED = 130


def run_guarded(func: Callable[[], object]) -> object:
    """Run ``func``; on Ctrl-C, exit at once with code 130.

    Commands save their state while the interrupt unwinds through them. What
    remained was Python waiting, at exit, for worker threads still inside a
    network call or a retry sleep: up to minutes of apparent hang after
    "Progress saved". Nothing is left to protect by then, so the process ends
    without waiting for them.
    """
    try:
        return func()
    except KeyboardInterrupt:
        pass
    except click.exceptions.Abort as exc:
        # click raises Abort for Ctrl-C, but also for a declined confirmation
        # or the end of input, which interrupt nothing.
        if not isinstance(exc.__context__, KeyboardInterrupt):
            raise
    get_console().print("[yellow]Interrupted. Progress is saved.[/yellow]")
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(EXIT_INTERRUPTED)


def run() -> None:
    """Console-script entry point for ``tepub``."""

    def invoke() -> object:
        # standalone_mode=False lets Ctrl-C reach run_guarded instead of being
        # turned into click's "Aborted!" and exit code 1.
        return app.main(standalone_mode=False)

    try:
        result = run_guarded(invoke)
    except click.exceptions.Exit as exc:
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        click.echo("Aborted!", err=True)
        sys.exit(1)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except TepubError as exc:
        # tepub's own errors say what is wrong and what to do; a damaged EPUB
        # used to end in a traceback. Other exceptions are defects and keep it.
        get_console().print(f"[red]{escape(str(exc))}[/red]")
        sys.exit(1)
    sys.exit(result if isinstance(result, int) else 0)


if __name__ == "__main__":
    run()
