"""Format command implementation."""

from pathlib import Path

import click

from cli.core import optional_book, settings_for_book
from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.store import load_state
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
    settings.ensure_directories()

    # Load state first: whether formatting applies depends on the language the
    # translations were actually produced in, which the state file records.
    # Deciding from current settings meant a config change after translating
    # either skipped formatting for Chinese output, or applied Chinese typography
    # to text translated into another language.
    try:
        state = load_state(settings.state_file)
    except FileNotFoundError:
        console.print("[red]State file not found. Run extract/translate first.[/red]")
        raise SystemExit(1)
    except (ValueError, TypeError, KeyError, CorruptedStateError) as exc:
        # Only FileNotFoundError was handled before, so a malformed or
        # schema-invalid state file surfaced as a raw traceback.
        console.print(f"[red]State file at {settings.state_file} is unreadable: {exc}[/red]")
        console.print("[yellow]Re-run extraction to rebuild it.[/yellow]")
        raise SystemExit(1) from exc

    recorded_target = getattr(state, "target_language", None) or settings.target_language
    if not target_is_chinese(recorded_target):
        console.print(
            f"[yellow]Translations target {recorded_target}, which is not Chinese; "
            f"nothing to format.[/yellow]"
        )
        return

    # Re-read and write under the state-file lock. This was an unlocked
    # read-modify-write, so a concurrent translate run's updates were overwritten.
    changed = update_state_atomic(settings.state_file, polish_state)

    if not changed:
        console.print("[green]Translations already formatted. No changes made.[/green]")
        return

    console.print(f"[green]Formatted translations saved to {settings.state_file}[/green]")
