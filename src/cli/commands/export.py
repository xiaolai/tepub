"""Export command implementation."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
from pydantic import BaseModel
from rich.markup import escape

from cli.core import EXIT_UNITS_FAILED, prepare_settings_for_epub
from cli.errors import handle_state_errors
from config import AppSettings
from config.workspace import epub_digest
from console_singleton import get_console
from injection.engine import Injection, apply_translations, run_injection
from state.base import atomic_write, safe_load_state
from state.models import SegmentStatus, StateDocument
from state.store import load_state, save_state
from state.writer import exclusive_run
from translation.languages import normalize_language
from translation.polish import polish_if_chinese
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
            type=click.Choice(
                ["bilingual", "translated-only", "translated_only"], case_sensitive=False
            ),
            hidden=True,
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _deprecated(old: str, new: str) -> None:
    console.print(
        f"[yellow]{escape(old)} is deprecated; use {escape(new)}. "
        "It will be removed in a later release.[/yellow]"
    )


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
    plan = ExportPlan(
        formats=tuple(dict.fromkeys(formats)) or ("epub",),
        modes=chosen,
        out_dir=out if out is not None else input_epub.resolve().parent,
        output_epub=output_epub,
    )
    if "epub" in plan.formats and output_epub is not None:
        # Only --output-epub can name the book itself; the language is unused there.
        for edition in plan.modes:
            if _is_the_book(_epub_path(plan, input_epub, "", edition), input_epub):
                raise click.UsageError(f"The output would overwrite the book itself: {input_epub}")
    return plan


def _is_the_book(path: Path, input_epub: Path) -> bool:
    """Whether writing `path` would replace the book. Compared as files, not
    names: on macOS /System/Volumes/Data/... and a case variant of the name
    resolve to other strings and are the same file."""
    try:
        return path.samefile(input_epub)
    except FileNotFoundError:
        return False


def _language_code(settings: AppSettings) -> str:
    """The translations' language, as the state records it."""
    if settings.state_file.exists():
        code = normalize_language(load_state(settings.state_file).target_language)[0]
    else:
        code = normalize_language(settings.target_language)[0]
    # The code names the output files and the web folder, which is replaced
    # whole; a code holding a path would write, and delete, somewhere else.
    if any(char in code for char in "/\\\0"):
        raise click.ClickException(
            f"The target language {code!r} cannot be part of a file name; "
            "use a language code such as zh-CN."
        )
    return code


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


def _web_dir(
    settings: AppSettings, plan: ExportPlan, input_epub: Path, lang: str, mode: str
) -> Path:
    # One edition keeps the plain name; with both, the bilingual one is marked
    # as its EPUB is.
    suffix = ".bilingual" if mode == "bilingual" and len(plan.modes) > 1 else ""
    return settings.work_dir / f"{input_epub.stem}.{lang}{suffix}.web"


def _check_destinations(
    settings: AppSettings, plan: ExportPlan, input_epub: Path, lang: str
) -> None:
    """Refuse destinations inside a web folder: the web export replaces the
    folder, deleting what was written there, and its archive would hold itself."""
    if "web" not in plan.formats:
        return
    targets = [plan.out_dir]
    if "epub" in plan.formats:
        targets += [_epub_path(plan, input_epub, lang, mode).parent for mode in plan.modes]
    for mode in plan.modes:
        site = _web_dir(settings, plan, input_epub, lang, mode).resolve()
        for target in targets:
            if target.resolve().is_relative_to(site):
                raise click.UsageError(
                    f"{target} is inside the web export's folder {site}, which is "
                    "replaced on export; write somewhere else."
                )


def _archive(site: Path, out_dir: Path) -> Path:
    """Zip `site` into `out_dir`; a failure leaves any previous archive whole."""
    final = out_dir / f"{site.name}.zip"
    partial: Path | None = None
    try:
        # Named per run: with one fixed name, a concurrent export could move
        # this run's half-written archive into place as its own.
        fd, name = tempfile.mkstemp(prefix=f".{site.name}.", suffix=".zip", dir=out_dir)
        os.close(fd)
        partial = Path(name)
        shutil.make_archive(
            str(partial.with_suffix("")), "zip", root_dir=site.parent, base_dir=site.name
        )
        os.replace(partial, final)
    except OSError as exc:
        if partial is not None:
            # The write's error is the one to report, not the cleanup's.
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)
        raise click.ClickException(f"Could not write {final}: {exc}") from exc
    return final


@dataclass(frozen=True)
class ExportResult:
    """The files written, and the translated units missing from them."""

    written: tuple[Path, ...]
    failed: tuple[str, ...]


class ExportRecord(BaseModel):
    """One file export wrote. `translations` fingerprints the finished
    translations it was written from, so a later change marks it stale;
    `size` and `sha256` identify the file written, so a file replaced at the
    path is not taken for it. Records from before they were kept lack them."""

    path: str
    language: str
    translations: str
    size: int | None = None
    sha256: str | None = None


class ExportsDocument(BaseModel):
    exports: list[ExportRecord] = []


def exports_file(settings: AppSettings) -> Path:
    return settings.work_dir / "exports.json"


def translations_fingerprint(state: StateDocument) -> str:
    """A digest of the finished translations, which is what an export holds."""
    digest = hashlib.sha256()
    for segment_id in sorted(state.segments):
        record = state.segments[segment_id]
        if record.status == SegmentStatus.COMPLETED:
            digest.update(f"{segment_id}\0{record.translation or ''}\0".encode())
    return digest.hexdigest()


def recorded_exports(settings: AppSettings) -> list[ExportRecord]:
    """What export recorded writing for this workspace; corrupt records raise."""
    path = exports_file(settings)
    if not path.exists():
        return []
    return safe_load_state(path, ExportsDocument, "exports").exports


def _record_exports(settings: AppSettings, lang: str, written: list[Path]) -> None:
    """Note the files written in the workspace: status looked for exports only
    beside the book, so it missed those written with --out, and could not tell
    an export older than the translations."""
    if not written or not settings.state_file.exists():
        return
    fingerprint = translations_fingerprint(load_state(settings.state_file))
    paths = {str(path.resolve()) for path in written}
    kept = [record for record in recorded_exports(settings) if record.path not in paths]
    added = [
        ExportRecord(
            path=p,
            language=lang,
            translations=fingerprint,
            size=Path(p).stat().st_size,
            sha256=epub_digest(Path(p)),
        )
        for p in sorted(paths)
    ]
    atomic_write(exports_file(settings), ExportsDocument(exports=kept + added).model_dump())


def run_exports(settings: AppSettings, input_epub: Path, plan: ExportPlan) -> ExportResult:
    """Write the plan's outputs; the paths written and the units left out."""
    # Every edition reads the segments and the state; held as extract and
    # translate hold it, so an extract between those reads cannot give one
    # export the units of one extraction and the translations of another.
    with exclusive_run(settings.state_file):
        return _run_exports(settings, input_epub, plan)


def _run_exports(settings: AppSettings, input_epub: Path, plan: ExportPlan) -> ExportResult:
    # Once for every edition; it ran inside each, taking the lock held here.
    polish_if_chinese(
        settings.state_file,
        settings.target_language,
        load_fn=load_state,
        save_fn=save_state,
        console_print=console.print,
        message_prefix="Before export:",
        holding_lock=True,
    )
    lang = _language_code(settings)
    _check_destinations(settings, plan, input_epub, lang)
    try:
        plan.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"Could not create {plan.out_dir}: {exc}") from exc
    written: list[Path] = []
    # Every edition holds the same units, so a unit missing from one is
    # missing from each; counted once.
    failed: dict[str, None] = {}
    # The EPUB and the web edition of a mode hold the same translated
    # documents; built once, the segments loaded and the book parsed once.
    injections: dict[str, Injection] = {}

    def injected(mode: str) -> Injection:
        if mode not in injections:
            injections[mode] = apply_translations(settings, input_epub, mode=mode)
        return injections[mode]

    if "epub" in plan.formats:
        for mode in plan.modes:
            path = _epub_path(plan, input_epub, lang, mode)
            injection = run_injection(
                settings, input_epub, path, mode=mode, injection=injected(mode)
            )
            failed.update(dict.fromkeys(injection.failed))
            if not injection.updated_html:
                if injection.failed:
                    console.print(
                        "[red]No translated unit could be inserted; no EPUB written.[/red]"
                    )
                else:
                    console.print("[yellow]Nothing is translated yet; no EPUB written.[/yellow]")
                break
            written.append(path)
    if "web" in plan.formats:
        # The browsable site stays in the workspace; its archive goes with
        # the other outputs.
        for mode in plan.modes:
            web = export_web(
                settings,
                input_epub,
                output_dir=_web_dir(settings, plan, input_epub, lang, mode),
                output_mode=mode,
                injection=injected(mode),
            )
            failed.update(dict.fromkeys(web.failed))
            written.append(_archive(web.site, plan.out_dir))
    for path in written:
        console.print(f"[green]Wrote {escape(str(path))}[/green]")
    _record_exports(settings, lang, written)
    if failed:
        console.print(
            f"[red]{len(failed)} translated units are missing from the export: they no "
            "longer match the book. Run extract again if the book changed.[/red]"
        )
    return ExportResult(tuple(written), tuple(failed))


@click.command(name="export")
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
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

    Exits with 3 when translated units could not be inserted.

    \b
    Files are named after the book and the language:
      book.zh-CN.bilingual.epub    original and translation together
      book.zh-CN.epub              translation only
      book.zh-CN.web.zip           a browsable web version
    """
    settings: AppSettings = ctx.obj["settings"]
    # Not created here: validation needs an extracted workspace.
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None, create=False)
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
    if run_exports(settings, input_epub, plan).failed:
        raise click.exceptions.Exit(EXIT_UNITS_FAILED)
