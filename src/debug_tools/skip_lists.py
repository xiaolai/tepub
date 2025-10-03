from __future__ import annotations

from rich.table import Table

from config import AppSettings
from state.models import SegmentStatus

from .common import console, load_all_segments, load_translation_state


def show_skip_list(settings: AppSettings) -> None:
    segments_doc = load_all_segments(settings)
    state = load_translation_state(settings)

    auto_skips = segments_doc.skipped_documents
    manual_skip_ids = [
        seg_id
        for seg_id, record in state.segments.items()
        if record.status == SegmentStatus.SKIPPED
    ]

    if not auto_skips and not manual_skip_ids:
        console.print("[green]No skipped segments found.[/green]")
        return

    if auto_skips:
        auto_table = Table(title="Automatically Skipped Documents")
        auto_table.add_column("File")
        auto_table.add_column("Reason")
        auto_table.add_column("Source")
        for skipped in auto_skips:
            auto_table.add_row(
                skipped.file_path.as_posix(),
                skipped.reason,
                skipped.source,
            )
        console.print(auto_table)

    if manual_skip_ids:
        segment_index = {segment.segment_id: segment for segment in segments_doc.segments}
        manual_table = Table(title="Skipped Segments")
        manual_table.add_column("Segment ID")
        manual_table.add_column("File")
        manual_table.add_column("Reason")
        for seg_id in manual_skip_ids:
            segment = segment_index.get(seg_id)
            if not segment:
                continue
            reason = state.segments[seg_id].error_message or segment.metadata.notes or ""
            manual_table.add_row(seg_id, segment.file_path.as_posix(), reason)
        console.print(manual_table)
