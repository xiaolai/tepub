from __future__ import annotations

from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from config import AppSettings
from state.models import SegmentStatus
from state.store import load_segments, load_state

from .common import console


def print_extraction_summary(
    settings: AppSettings,
    show_samples: int = 5,
    epub_path: Path | None = None,
) -> None:
    segments_doc = load_segments(settings.segments_file)
    state = load_state(settings.state_file)

    auto_skips = segments_doc.skipped_documents
    pending_ids = _collect_pending_ids(state)

    console.print(Panel.fit("Extraction Summary", style="bold"))

    # Show EPUB structure statistics
    if epub_path:
        _print_epub_statistics(epub_path, segments_doc, auto_skips)

    if auto_skips:
        # Separate TOC-based and cascade skips
        toc_skips = [s for s in auto_skips if s.source == "toc"]
        cascade_skips = [s for s in auto_skips if s.source == "cascade"]

        if toc_skips:
            auto_table = Table(title="Automatically Skipped Documents (TOC-based)")
            auto_table.add_column("File")
            auto_table.add_column("Reason")
            auto_table.add_column("Source")
            for skipped in toc_skips:
                auto_table.add_row(
                    skipped.file_path.as_posix(),
                    skipped.reason,
                    skipped.source,
                )
            console.print(auto_table)

        if cascade_skips:
            # Show cascade skip summary, not all files
            trigger_reasons = {}
            for skip in cascade_skips:
                reason = skip.reason
                trigger_reasons[reason] = trigger_reasons.get(reason, 0) + 1

            cascade_table = Table(title="Cascade Skipped Documents (Back-matter continuation)")
            cascade_table.add_column("Trigger", style="cyan")
            cascade_table.add_column("Files Skipped", style="bold yellow")

            for reason, count in sorted(trigger_reasons.items()):
                cascade_table.add_row(reason, str(count))

            console.print(cascade_table)
            console.print("[dim]  (Use --include-back-matter to process these files)[/dim]")
    else:
        console.print("[green]No automatic skips detected.[/green]")

    console.print(
        f"Pending segments: [bold]{len(pending_ids)}[/bold] (showing up to {show_samples})"
    )

    if pending_ids and show_samples > 0:
        sample_table = Table()
        sample_table.add_column("Segment ID")
        sample_table.add_column("File")
        sample_table.add_column("Status")

        segments_index = {segment.segment_id: segment for segment in segments_doc.segments}
        for seg_id in pending_ids[:show_samples]:
            segment = segments_index.get(seg_id)
            if not segment:
                continue
            sample_table.add_row(
                seg_id,
                segment.file_path.as_posix(),
                state.segments[seg_id].status.value,
            )
        console.print(sample_table)


def _collect_pending_ids(state) -> list[str]:
    return [
        seg_id
        for seg_id, record in state.segments.items()
        if record.status == SegmentStatus.PENDING
    ]


def _print_epub_statistics(epub_path: Path, segments_doc, auto_skips) -> None:
    """Print EPUB structure statistics (spine, TOC, processed files)."""
    try:
        from config import AppSettings
        from epub_io.reader import EpubReader
        from epub_io.resources import iter_spine_items
        from epub_io.selector import _flatten_toc_entries

        # Load EPUB to get spine and TOC counts
        temp_settings = AppSettings()
        reader = EpubReader(epub_path, temp_settings)

        spine_items = list(iter_spine_items(reader.book))
        toc_entries = _flatten_toc_entries(getattr(reader.book, "toc", []))

        # Count processed files
        processed_files = len(set(seg.file_path for seg in segments_doc.segments))
        skipped_files = len(auto_skips)
        total_handled = processed_files + skipped_files

        # Build statistics table
        stats_table = Table(title="EPUB Structure Statistics", show_header=False)
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Count", style="bold")

        stats_table.add_row("Total spine items", str(len(spine_items)))
        stats_table.add_row("TOC entries", str(len(toc_entries)))
        stats_table.add_row("Files processed", f"[green]{processed_files}[/green]")
        stats_table.add_row("Files skipped", f"[yellow]{skipped_files}[/yellow]")
        stats_table.add_row("Total handled", str(total_handled))

        # Warning if many files skipped
        if skipped_files > 0:
            skip_percentage = (skipped_files / total_handled) * 100
            if skip_percentage > 50:
                stats_table.add_row(
                    "⚠ Warning",
                    f"[red]{skip_percentage:.1f}% of files skipped[/red]",
                )

        console.print(stats_table)
        console.print()

    except Exception:
        # Silently fail if we can't load EPUB statistics
        pass
