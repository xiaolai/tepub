"""Format command implementation."""

from pathlib import Path

import click
from rich.markup import escape

from cli.core import optional_book, settings_for_book
from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.models import StateDocument
from state.writer import update_state_atomic
from translation.polish import polish_state, target_is_chinese

console = get_console()


@click.command()
@optional_book
@click.pass_context
def format_cmd(ctx: click.Context, book: Path | None) -> None:
    """Apply Chinese typography to a book's finished translations.

    Translate already does this for each reply; run it after editing the
    state by hand, or to apply a newer version of the rules.
    """

    settings: AppSettings = settings_for_book(ctx, book)
    # No ensure_directories(): formatting needs translations already on disk,
    # and creating the workspace first left an empty one behind the error.
    try:
        has_state = settings.state_file.exists()
    except OSError as exc:
        # A state file without permission raised a traceback here.
        console.print(
            f"[red]State file at {escape(str(settings.state_file))} could not be read: "
            f"{escape(str(exc))}[/red]"
        )
        raise SystemExit(1) from exc
    if not has_state:
        console.print("[red]State file not found. Run extract/translate first.[/red]")
        raise SystemExit(1)

    # Whether formatting applies depends on the language the translations were
    # actually produced in, which the state file records. Deciding from current
    # settings meant a config change after translating either skipped
    # formatting for Chinese output, or applied Chinese typography to text
    # translated into another language. It is decided on the document read
    # under the lock, so a state replaced since cannot be formatted unchecked.
    not_chinese: list[str] = []

    def polish_if_chinese(state: StateDocument) -> StateDocument | None:
        recorded_target = state.target_language or settings.target_language
        if not target_is_chinese(recorded_target):
            not_chinese.append(recorded_target)
            return None
        return polish_state(state)

    # Re-read and write under the state-file lock. This was an unlocked
    # read-modify-write, so a concurrent translate run's updates were overwritten.
    try:
        changed = update_state_atomic(settings.state_file, polish_if_chinese)
    except (OSError, ValueError, TypeError, KeyError, CorruptedStateError) as exc:
        # Reading, locking or saving can fail; each used to end in a traceback.
        console.print(
            f"[red]State file at {escape(str(settings.state_file))} could not be formatted: "
            f"{escape(str(exc))}[/red]"
        )
        raise SystemExit(1) from exc

    if not_chinese:
        console.print(
            f"[yellow]Translations target {escape(not_chinese[0])}, which is not Chinese; "
            f"nothing to format.[/yellow]"
        )
        return
    if not changed:
        console.print("[green]Translations already formatted. No changes made.[/green]")
        return

    console.print(
        f"[green]Formatted translations saved to {escape(str(settings.state_file))}[/green]"
    )
