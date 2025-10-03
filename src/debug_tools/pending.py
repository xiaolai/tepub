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
    for record in state.segments.values():
        if record.status == SegmentStatus.PENDING:
            segment = segment_index.get(record.segment_id)
            if segment:
                pending_by_file[segment.file_path.as_posix()] += 1

    if not pending_by_file:
        console.print("[green]No pending segments. All caught up![/green]")
        return

    table = Table(title="Pending Segments")
    table.add_column("File")
    table.add_column("Count")
    for file_path, count in sorted(pending_by_file.items()):
        table.add_row(file_path, str(count))

    console.print(table)
