"""Export command implementation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import click

from cli.core import prepare_settings_for_epub
from cli.errors import handle_state_errors
from config import AppSettings
from console_singleton import get_console
from injection.engine import run_injection
from state.store import load_state
from translation.languages import normalize_language
from webbuilder import export_web

console = get_console()

# --mode values, every spelling the config and the old flag accepted included.
_MODES = {
    "bilingual": ("bilingual",),
    "translated": ("translated_only",),
    "translated-only": ("translated_only",),
    "translated_only": ("translated_only",),
    "both": ("bilingual", "translated_only"),
}


@dataclass(frozen=True)
class ExportPlan:
    """What to write and where."""

    formats: tuple[str, ...]
    modes: tuple[str, ...]
    out_dir: Path
    # The deprecated --output-epub: the bilingual EPUB's own path.
    output_epub: Path | None = None


def export_options(func):
    """--mode, --format and --out, and the deprecated flags they replace;
    shared by export and pipeline."""
    options = [
        click.option(
            "--mode",
            type=click.Choice(sorted(_MODES), case_sensitive=False),
            help="Edition to write: bilingual, translated, or both. Default: the "
            "config's output_mode.",
        ),
        click.option(
            "--format",
            "formats",
            type=click.Choice(["epub", "web"], case_sensitive=False),
            multiple=True,
            help="epub (default) or web; give it twice for both.",
        ),
        click.option(
            "--out",
            type=click.Path(file_okay=False, path_type=Path),
            help="Folder to write to. Default: the book's own folder.",
        ),
        click.option("--epub", "epub_flag", is_flag=True, hidden=True),
        click.option("--web", "web_flag", is_flag=True, hidden=True),
        click.option("--output-epub", type=click.Path(path_type=Path), hidden=True),
        click.option(
            "--output-mode",
            type=click.Choice(["bilingual", "translated-only", "translated_only"], case_sensitive=False),
            hidden=True,
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _deprecated(old: str, new: str) -> None:
    console.print(f"[yellow]{old} is deprecated; use {new}. It will be removed in a later release.[/yellow]")


def resolve_export_plan(
    settings: AppSettings,
    input_epub: Path,
    *,
    mode: str | None,
    formats: tuple[str, ...],
    out: Path | None,
    epub_flag: bool = False,
    web_flag: bool = False,
    output_epub: Path | None = None,
    output_mode: str | None = None,
) -> ExportPlan:
    """The export to run, the deprecated flags mapped onto the new ones.

    The old flags misled: --epub ("only the translated EPUB") and
    --output-mode both wrote every edition and a web version, inside the
    workspace. They still work for this release, each with a warning.
    """
    formats = tuple(f.lower() for f in formats)
    if epub_flag or web_flag:
        _deprecated(
            "--epub" if epub_flag else "--web",
            "--format epub" if epub_flag and not web_flag else "--format web"
            if web_flag and not epub_flag else "--format epub --format web",
        )
        formats = formats + (("epub",) if epub_flag else ()) + (("web",) if web_flag else ())
    if output_mode:
        _deprecated("--output-mode", "--mode")
        mode = mode or output_mode
    if output_epub is not None:
        _deprecated("--output-epub", "--out DIR")
    if output_epub is not None and out is not None:
        raise click.UsageError("Give --out or the deprecated --output-epub, not both.")
    if mode is None and (epub_flag and not output_mode):
        mode = "both"  # what --epub always did
    chosen = _MODES[(mode or settings.output_mode).lower()]
    return ExportPlan(
        formats=tuple(dict.fromkeys(formats)) or ("epub",),
        modes=chosen,
        out_dir=out if out is not None else input_epub.resolve().parent,
        output_epub=output_epub,
    )


def _language_code(settings: AppSettings) -> str:
    """The translations' language, as the state records it."""
    if settings.state_file.exists():
        return normalize_language(load_state(settings.state_file).target_language)[0]
    return normalize_language(settings.target_language)[0]


def _epub_path(plan: ExportPlan, input_epub: Path, lang: str, mode: str) -> Path:
    if plan.output_epub is not None:
        # The deprecated --output-epub named the bilingual file; the
        # translated one went beside it.
        if mode == "bilingual":
            return plan.output_epub
        stem = plan.output_epub.stem.removesuffix("_bilingual")
        return plan.output_epub.with_name(f"{stem}_translated{plan.output_epub.suffix}")
    suffix = ".bilingual" if mode == "bilingual" else ""
    return plan.out_dir / f"{input_epub.stem}.{lang}{suffix}.epub"


def run_exports(settings: AppSettings, input_epub: Path, plan: ExportPlan) -> list[Path]:
    """Write the plan's outputs; the paths written."""
    lang = _language_code(settings)
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "epub" in plan.formats:
        for mode in plan.modes:
            path = _epub_path(plan, input_epub, lang, mode)
            updated, _ = run_injection(settings, input_epub, path, mode=mode)
            if not updated:
                console.print("[yellow]Nothing is translated yet; no EPUB written.[/yellow]")
                break
            written.append(path)
    if "web" in plan.formats:
        # The browsable site stays in the workspace; its archive goes with
        # the other outputs.
        site = export_web(
            settings,
            input_epub,
            output_dir=settings.work_dir / f"{input_epub.stem}.{lang}.web",
            output_mode=plan.modes[0],
        )
        archive = shutil.make_archive(
            str(plan.out_dir / site.name), "zip", root_dir=site.parent, base_dir=site.name
        )
        written.append(Path(archive))
    for path in written:
        console.print(f"[green]Wrote {path}[/green]")
    return written


@click.command(name="export")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@export_options
@click.pass_context
@handle_state_errors
def export_command(
    ctx: click.Context,
    input_epub: Path,
    mode: str | None,
    formats: tuple[str, ...],
    out: Path | None,
    epub_flag: bool,
    web_flag: bool,
    output_epub: Path | None,
    output_mode: str | None,
) -> None:
    """Write the translated book, next to the original by default.

    \b
    Files are named after the book and the language:
      book.zh-CN.bilingual.epub    original and translation together
      book.zh-CN.epub              translation only
      book.zh-CN.web.zip           a browsable web version
    """
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
    settings.validate_for_export(input_epub)
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
    run_exports(settings, input_epub, plan)
