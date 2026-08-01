"""Format command implementation."""

import click

from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.store import load_state, update_state_atomic
from translation.polish import polish_state, target_is_chinese

console = get_console()


@click.command()
@click.pass_context
def format_cmd(ctx: click.Context) -> None:
    """Format translated text for Chinese typography."""

    settings: AppSettings = ctx.obj["settings"]
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
        return
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
