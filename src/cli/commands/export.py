"""Export command implementation."""

from pathlib import Path

import click

from cli.core import (
    create_web_archive,
    derive_epub_paths,
    prepare_settings_for_epub,
    resolve_export_flags,
)
from cli.errors import handle_state_errors
from config import AppSettings
from console_singleton import get_console
from injection.engine import run_injection
from webbuilder import export_web

console = get_console()


def _run_exports(
    settings: AppSettings,
    input_epub: Path,
    export_epub: bool,
    web: bool,
    output_epub: Path | None,
) -> None:
    """Run EPUB and/or web exports based on flags.

    This shared function consolidates export logic used by both
    pipeline and export commands.
    """
    if not export_epub and not web:
        console.print("[yellow]No export option selected. Use --epub or --web.[/yellow]")
        return

    web_dir: Path | None = None
    web_archive: Path | None = None
    if web:
        web_dir = export_web(settings, input_epub, output_mode=settings.output_mode)
        web_archive = create_web_archive(web_dir)

    if export_epub:
        bilingual_epub, translated_epub = derive_epub_paths(
            input_epub, output_epub, settings.work_dir
        )
        if output_epub is None:
            console.print(
                f"[yellow]No --output-epub provided; writing bilingual EPUB to {bilingual_epub}[/yellow]"
            )

        updated_html, _ = run_injection(
            settings=settings,
            input_epub=input_epub,
            output_epub=bilingual_epub,
            mode="bilingual",
        )

        if updated_html:
            run_injection(
                settings=settings,
                input_epub=input_epub,
                output_epub=translated_epub,
                mode="translated_only",
            )
            console.print(f"[green]Exported bilingual EPUB to {bilingual_epub}[/green]")
            console.print(f"[green]Exported translated EPUB to {translated_epub}[/green]")
        else:
            console.print("[yellow]No translated segments found. EPUB export skipped.[/yellow]")

    if web_archive:
        console.print(f"[green]Exported web archive to {web_archive}[/green]")


@click.command(name="export")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--epub",
    "epub_flag",
    is_flag=True,
    help="Export only the translated EPUB output.",
)
@click.option(
    "--web",
    "web_flag",
    is_flag=True,
    help="Export only the web version.",
)
@click.option(
    "--output-epub",
    type=click.Path(path_type=Path),
    help="Optional EPUB output path; defaults to creating '<name>_bilingual.epub' and '<name>_translated.epub'.",
)
@click.option(
    "--output-mode",
    type=click.Choice(["bilingual", "translated-only"], case_sensitive=False),
    help="Select bilingual (default) or translated-only EPUB output.",
)
@click.pass_context
@handle_state_errors
def export_command(
    ctx: click.Context,
    input_epub: Path,
    epub_flag: bool,
    web_flag: bool,
    output_epub: Path | None,
    output_mode: str | None,
) -> None:
    """Export translated outputs (EPUB by default)."""

    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    if output_mode:
        settings = settings.model_copy(update={"output_mode": output_mode.replace("-", "_")})
        ctx.obj["settings"] = settings

    # Validate that required state files exist (errors handled by decorator)
    settings.validate_for_export(input_epub)

    export_epub, web = resolve_export_flags(epub_flag, web_flag)

    # _run_exports may also raise CorruptedStateError (handled by decorator)
    _run_exports(settings, input_epub, export_epub, web, output_epub)
