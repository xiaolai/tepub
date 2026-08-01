from __future__ import annotations

from collections import defaultdict

from rich.table import Table

from config import AppSettings
from state.models import SegmentStatus

from .common import console, load_all_segments, load_translation_state


def show_pending(settings: AppSettings) -> None:
    segments_doc = load_all_segments(settings)
    state = load_translation_state(settings)

    pending_by_file = defaultdict(int)
    segment_index = {segment.segment_id: segment for segment in segments_doc.segments}
    orphaned = 0
    auto_skipped = 0
    for record in state.segments.values():
        if record.status != SegmentStatus.PENDING:
            continue
        segment = segment_index.get(record.segment_id)
        if segment is None:
            # Records with no matching segment used to be dropped without a word,
            # so a state file out of sync with segments.json looked healthy.
            orphaned += 1
            continue
        if segment.skip_reason is not None:
            # Auto-skipped segments stay PENDING in state but translation filters
            # them out, so counting them meant the pending total never reached
            # zero no matter how many runs completed.
            auto_skipped += 1
            continue
        pending_by_file[segment.file_path.as_posix()] += 1

    if auto_skipped:
        console.print(
            f"[dim]Excluding {auto_skipped} auto-skipped segments that translation "
            f"does not process.[/dim]"
        )
    if orphaned:
        console.print(
            f"[yellow]{orphaned} pending records have no matching segment in "
            f"segments.json; the state file is out of sync — re-run extraction.[/yellow]"
        )

    if not pending_by_file:
        console.print("[green]No pending segments. All caught up![/green]")
        return

    table = Table(title="Pending Segments")
    table.add_column("File")
    table.add_column("Count")
    for file_path, count in sorted(pending_by_file.items()):
        table.add_row(file_path, str(count))

    console.print(table)
