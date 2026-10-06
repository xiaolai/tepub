"""Pipeline command implementation."""

from pathlib import Path

import click
from rich.markup import escape

from cli.commands.export import export_options, resolve_export_plan, run_exports
from cli.core import (
    EXIT_UNITS_FAILED,
    describe_pipeline_artifacts,
    prepare_settings_for_epub,
    translation_options,
    with_languages,
    with_provider,
)
from cli.errors import handle_run_errors
from config import AppSettings
from console_singleton import get_console
from extraction.pipeline import run_extraction
from translation.controller import run_translation

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@translation_options
@export_options
@click.pass_context
@handle_run_errors
def pipeline_command(
    ctx: click.Context,
    input_epub: Path,
    source_language: str | None,
    target_language: str | None,
    provider: str | None,
    model: str | None,
    allow_failures: bool,
    mode: str | None,
    formats: tuple[str, ...],
    out: Path | None,
    epub_flag: bool,
    web_flag: bool,
    output_epub: Path | None,
    output_mode: str | None,
) -> None:
    """Extract, translate and export a book in one go; resumable.

    Takes translate's and export's options. Exits with 3 when some units
    failed, or could not be inserted, after exporting the rest;
    --allow-failures exits with 0.
    """
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    settings = with_provider(settings, provider, model)
    # Before extraction, which records the languages in a new state: applied
    # after it, a run with --to kept the configured language when no unit was
    # left to translate, and exported under it.
    settings, source_code, target_code = with_languages(settings, source_language, target_language)
    ctx.obj["settings"] = settings
    plan = resolve_export_plan(
        settings,
        input_epub,
        mode=mode,
        formats=formats,
        out=out,
        epub_flag=epub_flag,
        web_flag=web_flag,
        output_epub=output_epub,
        output_mode=output_mode,
    )

    reusable, reason = describe_pipeline_artifacts(settings, input_epub)
    if reusable:
        console.print(
            "[cyan]Resuming with the existing workspace at "
            f"{escape(str(settings.work_dir))}[/cyan]"
        )
    else:
        # Say why we are re-extracting. Every failure mode used to collapse into a
        # silent full re-run, which looked identical whether the workspace was
        # merely absent or belonged to a different book entirely.
        console.print(f"[yellow]Extracting: {escape(str(reason))}.[/yellow]")
        run_extraction(settings=settings, input_epub=input_epub)

    summary = run_translation(
        settings=settings,
        input_epub=input_epub,
        source_language=source_code,
        target_language=target_code,
    )
    export = run_exports(settings, input_epub, plan)
    if (summary.failed or export.failed) and not allow_failures:
        raise click.exceptions.Exit(EXIT_UNITS_FAILED)
