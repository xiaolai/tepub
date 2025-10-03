"""Translate command implementation."""

from pathlib import Path

import click

from cli.core import prepare_settings_for_epub
from cli.errors import handle_state_errors
from config import AppSettings
from translation.controller import run_translation
from translation.languages import normalize_language


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option("--from", "source_language", help="Source language (code or name).", default=None)
@click.option(
    "--to",
    "target_language",
    help="Target language (code or name).",
    default=None,
)
@click.pass_context
@handle_state_errors
def translate(
    ctx: click.Context,
    input_epub: Path,
    source_language: str | None,
    target_language: str | None,
) -> None:
    """Translate pending segments using configured provider."""

    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)

    # Validate that required files exist for translation (errors handled by decorator)
    settings.validate_for_translation(input_epub)

    source_pref = source_language or settings.source_language
    target_pref = target_language or settings.target_language
    source_code, _source_display = normalize_language(source_pref)
    target_code, _target_display = normalize_language(target_pref)

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
