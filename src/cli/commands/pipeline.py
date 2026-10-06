"""Pipeline command implementation."""

from pathlib import Path

import click

from cli.commands.export import export_options, resolve_export_plan, run_exports
from cli.core import (
    EXIT_UNITS_FAILED,
    describe_pipeline_artifacts,
    prepare_settings_for_epub,
    translation_options,
    with_provider,
)
from cli.errors import handle_run_errors
from config import AppSettings
from console_singleton import get_console
from extraction.pipeline import run_extraction
from translation.controller import run_translation
from translation.languages import normalize_language

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
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
    failed, after exporting the rest; --allow-failures exits with 0.
    """
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    settings = with_provider(settings, provider, model)
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
        console.print(f"[cyan]Resuming with the existing workspace at {settings.work_dir}[/cyan]")
    else:
        # Say why we are re-extracting. Every failure mode used to collapse into a
        # silent full re-run, which looked identical whether the workspace was
        # merely absent or belonged to a different book entirely.
        console.print(f"[yellow]Extracting: {reason}.[/yellow]")
        run_extraction(settings=settings, input_epub=input_epub)

    source_pref = source_language or settings.source_language
    target_pref = target_language or settings.target_language
    source_code, _ = normalize_language(source_pref)
    target_code, _ = normalize_language(target_pref)
    settings = settings.model_copy(
        update={"source_language": source_pref, "target_language": target_pref}
    )
    ctx.obj["settings"] = settings

    summary = run_translation(
        settings=settings,
        input_epub=input_epub,
        source_language=source_code,
        target_language=target_code,
    )
    run_exports(settings, input_epub, plan)
    if summary.failed and not allow_failures:
        raise click.exceptions.Exit(EXIT_UNITS_FAILED)
