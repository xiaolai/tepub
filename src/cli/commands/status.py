"""Status command: where a book stands, and what to do next."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

import click

from cli.core import optional_book, settings_for_book
from config import AppSettings
from console_singleton import get_console
from exceptions import CorruptedStateError
from state.models import SegmentStatus
from state.store import load_segments, load_state
from translation.controller import select_for_translation
from translation.languages import describe_language

console = get_console()

FAILED_SHOWN = 5


def _outputs(book: Path | None) -> list[Path]:
    if book is None:
        return []
    source = book.resolve()
    return sorted(
        p
        for p in source.parent.glob(f"{book.stem}.*")
        if p != source and p.suffix in (".epub", ".zip")
    )


def show_status(ctx: click.Context, book: Path | None) -> None:
    settings: AppSettings = settings_for_book(ctx, book)
    name = book.name if book is not None else "<book.epub>"
    if not settings.segments_file.exists():
        console.print(f"Not extracted yet. Next: [bold]tepub extract {name}[/bold]")
        raise SystemExit(1)
    try:
        segments_doc = load_segments(settings.segments_file)
        state = load_state(settings.state_file) if settings.state_file.exists() else None
    except (OSError, ValueError, TypeError, KeyError, CorruptedStateError) as exc:
        console.print(f"[red]The workspace at {settings.work_dir} is unreadable: {exc}[/red]")
        raise SystemExit(1) from exc

    units = select_for_translation(segments_doc.segments, settings)
    records = state.segments if state is not None else {}
    status = Counter(
        getattr(records.get(u.segment_id), "status", SegmentStatus.PENDING) for u in units
    )
    done, failed = status[SegmentStatus.COMPLETED], status[SegmentStatus.ERROR]
    pending = len(units) - done - failed
    engines = Counter(
        f"{r.provider_name} / {r.model_name}"
        for u in units
        if (r := records.get(u.segment_id)) is not None
        and r.status == SegmentStatus.COMPLETED
        and r.provider_name
    )

    title = segments_doc.book_title or name
    console.print(f"[bold]{title}[/bold]")
    console.print(f"  Workspace   {settings.work_dir}")
    if state is not None:
        console.print(
            f"  Languages   {describe_language(state.source_language)} → "
            f"{describe_language(state.target_language)}"
        )
    provider = settings.primary_provider
    console.print(f"  Engine      {provider.name} / {provider.model} (configured)")
    if engines:
        used = ", ".join(f"{engine} ({count})" for engine, count in engines.most_common())
        console.print(f"  Translated  with {used}")
    console.print(
        f"  Units       {done:,} of {len(units):,} translated"
        + (f" · [red]{failed} failed[/red]" if failed else "")
        + (f" · {pending:,} pending" if pending else "")
    )

    from glossary import glossary_for

    target = state.target_language if state is not None else settings.target_language
    glossary = glossary_for(settings.work_root, settings.work_dir, target)
    if glossary is not None and state is not None:
        from glossary.report import find_misses

        missed = len({m.segment_id for m in find_misses(units, state, glossary)})
        console.print(
            f"  Glossary    {len(glossary.decided())} terms"
            + (f" · {missed} units miss a rendering" if missed else "")
        )
    for output in _outputs(book):
        stamp = datetime.fromtimestamp(output.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print(f"  Output      {output.name}  ({stamp})")

    failed_units = [
        (unit.segment_id, records[unit.segment_id].error_message or "")
        for unit in units
        if unit.segment_id in records and records[unit.segment_id].status == SegmentStatus.ERROR
    ]
    if failed_units:
        console.print(f"\nFailed units ({len(failed_units)}):")
        for segment_id, message in failed_units[:FAILED_SHOWN]:
            console.print(f"  {segment_id}: {message[:100]}")
        if len(failed_units) > FAILED_SHOWN:
            console.print(f"  … and {len(failed_units) - FAILED_SHOWN} more")

    if pending or failed:
        console.print(f"\nNext: [bold]tepub translate {name}[/bold]")
    elif not _outputs(book):
        console.print(f"\nNext: [bold]tepub export {name}[/bold]")
    else:
        console.print("\nDone.")


@click.command()
@optional_book
@click.pass_context
def status(ctx: click.Context, book: Path | None) -> None:
    """Show where a book stands: units, failures, glossary, outputs, next step."""
    show_status(ctx, book)
