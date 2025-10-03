"""Format command implementation."""

import click

from config import AppSettings
from console_singleton import get_console
from state.store import load_state, save_state
from translation.polish import polish_state, target_is_chinese

console = get_console()


@click.command()
@click.pass_context
def format_cmd(ctx: click.Context) -> None:
    """Format translated text for Chinese typography."""

    settings: AppSettings = ctx.obj["settings"]
    settings.ensure_directories()

    if not target_is_chinese(settings.target_language):
        console.print("[yellow]Target language is not Chinese; nothing to format.[/yellow]")
        return

    try:
        state = load_state(settings.state_file)
    except FileNotFoundError:
        console.print("[red]State file not found. Run extract/translate first.[/red]")
        return

    polished = polish_state(state)
    if polished.model_dump() == state.model_dump():
        console.print("[green]Translations already formatted. No changes made.[/green]")
        return

    save_state(polished, settings.state_file)
    console.print(f"[green]Formatted translations saved to {settings.state_file}[/green]")
