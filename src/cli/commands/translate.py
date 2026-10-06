"""Translate command implementation."""

from pathlib import Path

import click

from cli.core import (
    EXIT_UNITS_FAILED,
    prepare_settings_for_epub,
    translation_options,
    with_languages,
    with_provider,
)
from cli.errors import handle_run_errors, handle_state_errors
from config import AppSettings
from console_singleton import get_console
from state.base import safe_load_state
from state.models import StateDocument
from translation.controller import plan_translation, run_translation

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
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
    # Not created here: validation needs an extracted workspace, and the run
    # creates what it writes to; a dry run, or a failed check, left folders behind.
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None, create=False)
    settings.validate_for_translation(input_epub)
    if settings.state_file.exists():
        # A damaged state file is reported as such, not as a traceback from
        # the plan or the run that read it.
        safe_load_state(settings.state_file, StateDocument, "translation")
    settings = with_provider(settings, provider, model)

    settings, source_code, target_code = with_languages(settings, source_language, target_language)
    ctx.obj["settings"] = settings

    if dry_run:
        plan = plan_translation(settings, input_epub)
        # Counted as status counts pending units; units of punctuation or
        # numbers alone are copied, not sent, and said so.
        copied = f", {plan.copied:,} of them copied as they are" if plan.copied else ""
        console.print(
            f"{plan.units:,} units to translate{copied}: {plan.characters:,} characters "
            f"for {plan.provider} / {plan.model}, into {settings.target_language}"
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
