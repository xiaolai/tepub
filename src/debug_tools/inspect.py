from __future__ import annotations

from rich.panel import Panel

from config import AppSettings

from .common import console, load_all_segments, load_translation_state


def inspect_segment(settings: AppSettings, segment_id: str) -> None:
    segments_doc = load_all_segments(settings)
    state = load_translation_state(settings)

    segment = next((seg for seg in segments_doc.segments if seg.segment_id == segment_id), None)
    if not segment:
        console.print(f"[red]Segment {segment_id} not found in segments file.[/red]")
        return

    record = state.segments.get(segment_id)
    if not record:
        console.print(f"[yellow]No translation state found for segment {segment_id}.[/yellow]")
        return

    console.print(
        Panel.fit(
            f"File: {segment.file_path}\nXPath: {segment.xpath}\nMode: {segment.extract_mode}\nElement: {segment.metadata.element_type}\nStatus: {record.status}",
            title=f"Segment {segment_id}",
        )
    )
    console.print(Panel(segment.source_content, title="Original"))
    if record.translation:
        console.print(Panel(record.translation, title="Translation"))
    if record.error_message:
        console.print(Panel(record.error_message, title="Error", subtitle_align="left"))
