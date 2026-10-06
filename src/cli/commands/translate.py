"""Translate command implementation."""

from pathlib import Path

import click

from cli.core import (
    EXIT_UNITS_FAILED,
    prepare_settings_for_epub,
    translation_options,
    with_provider,
)
from console_singleton import get_console
from cli.errors import handle_run_errors, handle_state_errors
from config import AppSettings
from translation.controller import plan_translation, run_translation
from translation.languages import normalize_language

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@translation_options
@click.option("--dry-run", is_flag=True, help="Report what would be translated, and stop.")
@click.pass_context
@handle_state_errors
@handle_run_errors
def translate(
    ctx: click.Context,
    input_epub: Path,
    source_language: str | None,
    target_language: str | None,
    provider: str | None,
    model: str | None,
    allow_failures: bool,
    dry_run: bool,
) -> None:
    """Translate the book's remaining units; resumable.

    Ends with a summary. Exits with 3 when some units failed, so a script
    can tell; --allow-failures exits with 0. Run it again to retry them.
    """
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    settings.validate_for_translation(input_epub)
    settings = with_provider(settings, provider, model)

    source_pref = source_language or settings.source_language
    target_pref = target_language or settings.target_language
    source_code, _source_display = normalize_language(source_pref)
    target_code, _target_display = normalize_language(target_pref)
    settings = settings.model_copy(
        update={"source_language": source_pref, "target_language": target_pref}
    )
    ctx.obj["settings"] = settings

    if dry_run:
        plan = plan_translation(settings, input_epub)
        console.print(
            f"{plan.units} units to translate, {plan.characters:,} characters, into "
            f"{target_pref} with {plan.provider} / {plan.model}"
            + (f", holding {plan.glossary_terms} glossary terms." if plan.glossary_terms else ".")
        )
        return

    summary = run_translation(
        settings=settings,
        input_epub=input_epub,
        source_language=source_code,
        target_language=target_code,
    )
    if summary.failed and not allow_failures:
        raise click.exceptions.Exit(EXIT_UNITS_FAILED)
