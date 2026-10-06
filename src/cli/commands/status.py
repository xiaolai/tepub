"""Status command: where a book stands, and what to do next."""

from __future__ import annotations

import os
import shlex
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.markup import escape

from cli.core import optional_book, settings_for_book
from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.models import Segment, SegmentsDocument, SegmentStatus, StateDocument
from state.store import load_segments, load_state
from translation.controller import select_for_translation
from translation.languages import describe_language, normalize_language

if TYPE_CHECKING:
    from cli.commands.export import ExportRecord

console = get_console()

FAILED_SHOWN = 5


def _beside_book(book: Path | None, language: str) -> list[Path]:
    """Where export puts the book's outputs in this language when not told
    otherwise, whether or not they exist.

    Matching a glob of the book's stem read characters such as brackets as a
    pattern, and counted folders, other languages and unrelated archives.
    """
    if book is None:
        return []
    folder, stem = book.resolve().parent, book.stem
    lang = normalize_language(language)[0]
    names = (f"{stem}.{lang}.bilingual.epub", f"{stem}.{lang}.epub", f"{stem}.{lang}.web.zip")
    return [folder / name for name in names]


def _command(ctx: click.Context, step: str, book: Path | None, *options: str) -> str:
    """A command line to copy: the book's full path and this run's --config and
    --work-dir, without which it would act on another workspace."""
    words = ["tepub"]
    if ctx.obj.get("config_file"):
        words += ["--config", str(ctx.obj["config_file"])]
    if ctx.obj.get("work_dir_override_path"):
        words += ["--work-dir", str(ctx.obj["work_dir_override_path"])]
    words += [step, *options]
    if book is None:
        return shlex.join(words) + " <book.epub>"
    # A book named like "-x.epub" was read as an option.
    if str(book).startswith("-"):
        words.append("--")
    return shlex.join([*words, str(book)])


def _language_options(state: StateDocument | None, settings: AppSettings) -> tuple[str, ...]:
    """--from and --to when the translations' languages are not the configured
    ones: translate restarts a book whose languages change."""
    if state is None:
        return ()
    recorded = (state.source_language, state.target_language)
    configured = (settings.source_language, settings.target_language)
    codes = [normalize_language(language)[0] for language in (*recorded, *configured)]
    if codes[:2] == codes[2:]:
        return ()
    return ("--from", recorded[0], "--to", recorded[1])


def _load(settings: AppSettings) -> tuple[SegmentsDocument, StateDocument | None]:
    """The workspace's segments and, once translation began, its state."""
    try:
        segments_doc = load_segments(settings.segments_file)
        state = load_state(settings.state_file) if settings.state_file.exists() else None
    except (OSError, ValueError, TypeError, KeyError, CorruptedStateError) as exc:
        console.print(
            f"[red]The workspace at {escape(str(settings.work_dir))} is unreadable: "
            f"{escape(str(exc))}[/red]"
        )
        raise SystemExit(1) from exc
    return segments_doc, state


def _recorded_book(settings: AppSettings, segments_doc: SegmentsDocument) -> Path | None:
    """The book the workspace was extracted from, when it can still be found.

    Its outputs were never looked for without the book, so a finished book was
    always sent to export. The path is recorded as given to extract, so a
    relative one is also looked for beside the workspace, where a book's own
    workspace is made; a file found there must be the book extracted.
    """
    from config.workspace import epub_digest

    recorded = Path(str(segments_doc.epub_path))
    candidates = [recorded]
    if not recorded.is_absolute():
        candidates.append(settings.work_dir.parent / recorded.name)
    for candidate in candidates:
        try:
            if candidate.is_file() and segments_doc.epub_sha256 in (
                None,
                epub_digest(candidate),
            ):
                return candidate
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class _Progress:
    """Where the units selected for translation stand."""

    units: list[Segment]
    done: int
    failed: int
    pending: int
    engines: Counter[str]
    failures: list[tuple[str, str]]


def _progress(
    segments_doc: SegmentsDocument, state: StateDocument | None, settings: AppSettings
) -> _Progress:
    units = select_for_translation(segments_doc.segments, settings)
    records = state.segments if state is not None else {}
    status = Counter(
        getattr(records.get(u.segment_id), "status", SegmentStatus.PENDING) for u in units
    )
    done, failed = status[SegmentStatus.COMPLETED], status[SegmentStatus.ERROR]
    engines = Counter(
        f"{r.provider_name} / {r.model_name}"
        for u in units
        if (r := records.get(u.segment_id)) is not None
        and r.status == SegmentStatus.COMPLETED
        and r.provider_name
    )
    failures = [
        (unit.segment_id, records[unit.segment_id].error_message or "")
        for unit in units
        if unit.segment_id in records and records[unit.segment_id].status == SegmentStatus.ERROR
    ]
    return _Progress(units, done, failed, len(units) - done - failed, engines, failures)


def _glossary_line(
    settings: AppSettings, state: StateDocument | None, units: list[Segment], target: str
) -> str:
    from glossary import glossary_for
    from glossary.model import GlossaryError

    try:
        glossary = glossary_for(settings.work_root, settings.work_dir, target)
    except (GlossaryError, OSError) as exc:
        return f"[yellow]{escape(str(exc))}[/yellow]"
    if glossary is None:
        return ""
    missed = 0
    if state is not None:
        from glossary.report import find_misses

        missed = len({m.segment_id for m in find_misses(units, state, glossary)})
    return f"{len(glossary.decided())} terms" + (
        f" · {missed} units miss a rendering" if missed else ""
    )


@dataclass(frozen=True)
class _Output:
    """A file export wrote, or may have written, and whether it is current."""

    shown: str
    note: str  # markup: when it was written, and what is wrong with it
    current: bool


def _find_outputs(
    settings: AppSettings, book: Path | None, state: StateDocument | None, target: str
) -> tuple[list[_Output], str | None]:
    """The exports in the translations' language, and a problem with export's
    record of them.

    Exports are found where export recorded writing them, --out included, and
    beside the book by name. Only a recorded export can be checked against the
    translations it holds, so one written before the record was kept, or by
    something else, is listed without counting as current. A path that is now
    a folder is no export.
    """
    from cli.commands.export import recorded_exports, translations_fingerprint

    lang = normalize_language(target)[0]
    problem = None
    try:
        exported = {Path(r.path): r for r in recorded_exports(settings) if r.language == lang}
    except (OSError, CorruptedStateError) as exc:
        exported, problem = {}, str(exc)
    fingerprint = translations_fingerprint(state) if state is not None else None
    candidates = list(exported) + [
        path for path in _beside_book(book, target) if _resolved(path) not in exported
    ]
    folder = book.resolve().parent if book is not None else None
    outputs: list[_Output] = []
    for path in candidates:
        shown = path.name if path.parent == folder else str(path)
        try:
            info = path.stat()
        except FileNotFoundError:
            continue  # never written, or moved or deleted since
        except OSError as exc:
            # Unreadable, or in a folder without permission: listed, not counted.
            note = f"[yellow]unreadable: {escape(str(exc))}[/yellow]"
            outputs.append(_Output(shown, note, False))
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        stamp = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
        record = exported.get(path)
        if record is None:
            note = f"({stamp}) · [yellow]not recorded by export; may be out of date[/yellow]"
            outputs.append(_Output(shown, note, False))
        elif record.translations != fingerprint:
            note = f"({stamp}) · [yellow]older than the translations[/yellow]"
            outputs.append(_Output(shown, note, False))
        elif (changed := _changed_since_export(path, info, record)) is not None:
            outputs.append(_Output(shown, f"({stamp}) · [yellow]{changed}[/yellow]", False))
        else:
            outputs.append(_Output(shown, f"({stamp})", True))
    return outputs, problem


def _resolved(path: Path) -> Path | None:
    """The path with links followed, or None when they loop: a symlink loop
    raised from resolve() and stopped status."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):  # RuntimeError before Python 3.13
        return None


def _changed_since_export(path: Path, info: os.stat_result, record: ExportRecord) -> str | None:
    """Why the file at a recorded path may not be what export wrote, or None.

    The path alone was trusted, so a file replaced there by anything else
    counted as the current export. Records from before export noted the
    file's size and digest cannot be checked, and count as unrecorded files do.
    """
    from config.workspace import epub_digest

    if record.size is None or record.sha256 is None:
        return "recorded without its contents; may be out of date"
    if info.st_size != record.size:
        return "changed since export"
    try:
        digest = epub_digest(path)
    except OSError as exc:
        return f"unreadable: {escape(str(exc))}"
    return None if digest == record.sha256 else "changed since export"


def _next_step(
    ctx: click.Context,
    book: Path | None,
    settings: AppSettings,
    state: StateDocument | None,
    progress: _Progress,
    outputs: list[_Output],
) -> str | None:
    """The command to run next, or None when the book is done."""
    if progress.pending or progress.failed:
        return _command(ctx, "translate", book, *_language_options(state, settings))
    if not any(output.current for output in outputs):
        return _command(ctx, "export", book)
    return None


def show_status(ctx: click.Context, book: Path | None) -> None:
    settings: AppSettings = settings_for_book(ctx, book)
    try:
        extracted = settings.segments_file.exists()
    except OSError:
        extracted = True  # let _load report what is wrong with it
    if not extracted:
        step = _command(ctx, "extract", book)
        console.print(f"Not extracted yet. Next: [bold]{escape(step)}[/bold]")
        raise SystemExit(1)
    segments_doc, state = _load(settings)
    if book is None:
        book = _recorded_book(settings, segments_doc)

    progress = _progress(segments_doc, state, settings)
    target = state.target_language if state is not None else settings.target_language
    glossary = _glossary_line(settings, state, progress.units, target)
    outputs, output_problem = _find_outputs(settings, book, state, target)

    # Titles, messages and paths are printed escaped: as markup, a "[/bold]" in
    # one stopped the command with a MarkupError.
    title = segments_doc.book_title or (book.name if book is not None else "<book.epub>")
    console.print(f"[bold]{escape(title)}[/bold]")
    console.print(f"  Workspace   {escape(str(settings.work_dir))}")
    if state is not None:
        console.print(
            f"  Languages   {escape(describe_language(state.source_language))} → "
            f"{escape(describe_language(state.target_language))}"
        )
    provider = settings.primary_provider
    console.print(f"  Engine      {escape(f'{provider.name} / {provider.model}')} (configured)")
    if progress.engines:
        used = ", ".join(f"{engine} ({n})" for engine, n in progress.engines.most_common())
        console.print(f"  Translated  with {escape(used)}")
    console.print(
        f"  Units       {progress.done:,} of {len(progress.units):,} translated"
        + (f" · [red]{progress.failed} failed[/red]" if progress.failed else "")
        + (f" · {progress.pending:,} pending" if progress.pending else "")
    )
    if glossary:
        console.print(f"  Glossary    {glossary}")
    if output_problem is not None:
        console.print(f"  Output      [yellow]{escape(output_problem)}[/yellow]")
    for output in outputs:
        console.print(f"  Output      {escape(output.shown)}  {output.note}")

    if progress.failures:
        console.print(f"\nFailed units ({len(progress.failures)}):")
        for segment_id, message in progress.failures[:FAILED_SHOWN]:
            console.print(f"  {escape(segment_id)}: {escape(message[:100])}")
        if len(progress.failures) > FAILED_SHOWN:
            console.print(f"  … and {len(progress.failures) - FAILED_SHOWN} more")

    step = _next_step(ctx, book, settings, state, progress, outputs)
    console.print(f"\nNext: [bold]{escape(step)}[/bold]" if step else "\nDone.")


@click.command()
@optional_book
@click.pass_context
def status(ctx: click.Context, book: Path | None) -> None:
    """Show where a book stands: units, failures, glossary, outputs, next step."""
    show_status(ctx, book)
