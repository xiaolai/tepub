from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from rich.panel import Panel
from rich.progress import Progress
from rich.table import Table

from config import AppSettings
from epub_io.selector import analyze_skip_candidates

from .common import console


def _iter_epubs(library: Path) -> Iterable[Path]:
    if library.is_file() and library.suffix.lower() == ".epub":
        yield library
        return

    for path in sorted(library.rglob("*.epub")):
        if path.is_file():
            yield path


def analyze_library(
    settings: AppSettings,
    library: Path,
    limit: int | None = None,
    top_n: int = 15,
    report_path: Path | None = None,
) -> None:
    library = library.expanduser()
    if not library.exists():
        console.print(f"[bold red]Library path not found:[/bold red] {library}")
        raise SystemExit(1)

    epubs = list(_iter_epubs(library))
    if not epubs:
        console.print(f"[yellow]No EPUB files found under {library}.[/yellow]")
        return

    if limit is not None:
        epubs = epubs[: limit if limit >= 0 else 0]

    reason_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    unmatched_counter: Counter[str] = Counter()
    processed = 0
    books_with_skips = 0
    errors: list[tuple[Path, str]] = []

    with Progress() as progress:
        task = progress.add_task("Analyzing", total=len(epubs))
        for epub_path in epubs:
            try:
                analysis = analyze_skip_candidates(epub_path, settings)
            except Exception as exc:  # pragma: no cover - surfaced in output
                errors.append((epub_path, str(exc)))
                progress.advance(task)
                continue

            candidates = [c for c in analysis.candidates if c.flagged]
            if candidates:
                books_with_skips += 1
                reason_counter.update(c.reason for c in candidates)
                source_counter.update(c.source for c in candidates)
            unmatched_counter.update(analysis.toc_unmatched_titles)

            processed += 1
            progress.advance(task)

    console.print(
        Panel(
            f"Processed {processed} EPUBs | {books_with_skips} with skips | "
            f"{len(errors)} errors",
            title="Skip Analysis",
        )
    )

    if reason_counter:
        table = Table(title="Skip Reasons", show_lines=False)
        table.add_column("Reason")
        table.add_column("Count", justify="right")
        for reason, count in reason_counter.most_common():
            table.add_row(reason, str(count))
        console.print(table)

    if source_counter:
        source_table = Table(title="Skip Sources", show_lines=False)
        source_table.add_column("Source")
        source_table.add_column("Count", justify="right")
        for source, count in source_counter.most_common():
            source_table.add_row(source, str(count))
        console.print(source_table)

    if unmatched_counter:
        unmatched_table = Table(title=f"Potential New Keywords (top {top_n})")
        unmatched_table.add_column("Title")
        unmatched_table.add_column("Count", justify="right")
        for title, count in unmatched_counter.most_common(top_n):
            unmatched_table.add_row(title, str(count))
        console.print(unmatched_table)

    if errors:
        error_table = Table(title="Errors", show_lines=False)
        error_table.add_column("EPUB")
        error_table.add_column("Error")
        for path, message in errors[:10]:
            error_table.add_row(path.as_posix(), message)
        console.print(error_table)

    if report_path:
        payload = {
            "processed": processed,
            "books_with_skips": books_with_skips,
            "errors": [{"path": str(path), "error": message} for path, message in errors],
            "reasons": dict(reason_counter),
            "sources": dict(source_counter),
            "unmatched_titles": dict(unmatched_counter),
        }
        report_path = report_path.expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Report written to {report_path}[/green]")
