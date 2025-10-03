"""Pipeline command implementation."""

from pathlib import Path

import click

from cli.commands.export import _run_exports
from cli.core import check_pipeline_artifacts, prepare_settings_for_epub, resolve_export_flags
from config import AppSettings
from console_singleton import get_console
from extraction.pipeline import run_extraction
from translation.controller import run_translation
from translation.languages import normalize_language

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option("--from", "source_language", default=None, help="Source language (code or name).")
@click.option("--to", "target_language", default=None, help="Target language (code or name).")
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
def pipeline_command(
    ctx: click.Context,
    input_epub: Path,
    source_language: str | None,
    target_language: str | None,
    epub_flag: bool,
    web_flag: bool,
    output_epub: Path | None,
    output_mode: str | None,
) -> None:
    """Run extraction, translation, and export in one go."""

    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    if output_mode:
        settings = settings.model_copy(update={"output_mode": output_mode.replace("-", "_")})
        ctx.obj["settings"] = settings

    if check_pipeline_artifacts(settings, input_epub):
        console.print(
            f"[cyan]Resuming with existing extraction at {settings.segments_file}; using state {settings.state_file}[/cyan]"
        )
    else:
        run_extraction(settings=settings, input_epub=input_epub)

    source_pref = source_language or settings.source_language
    target_pref = target_language or settings.target_language
    source_code, _ = normalize_language(source_pref)
    target_code, _ = normalize_language(target_pref)

    settings = settings.model_copy(
        update={
            "source_language": source_pref,
            "target_language": target_pref,
        }
    )
    ctx.obj["settings"] = settings

    run_translation(
        settings=settings,
        input_epub=input_epub,
        source_language=source_code,
        target_language=target_code,
    )

    export_epub, web = resolve_export_flags(epub_flag, web_flag)
    _run_exports(settings, input_epub, export_epub, web, output_epub)
