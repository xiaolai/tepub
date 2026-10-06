from __future__ import annotations

from rich.markup import escape
from rich.panel import Panel

from config import AppSettings

from .common import console, load_all_segments, load_translation_state


def inspect_segment(settings: AppSettings, segment_id: str) -> None:
    segments_doc = load_all_segments(settings)
    state = load_translation_state(settings)

    segment = next((seg for seg in segments_doc.segments if seg.segment_id == segment_id), None)
    if not segment:
        console.print(f"[red]Segment {escape(str(segment_id))} not found in segments file.[/red]")
        return

    record = state.segments.get(segment_id)
    if not record:
        console.print(
            f"[yellow]No translation state found for segment {escape(str(segment_id))}.[/yellow]"
        )
        return

    console.print(
        Panel.fit(
            f"File: {escape(str(segment.file_path))}\nXPath: {escape(str(segment.xpath))}\n"
            f"Mode: {escape(str(segment.extract_mode))}\n"
            f"Element: {escape(str(segment.metadata.element_type))}\n"
            f"Status: {escape(str(record.status))}",
            title=f"Segment {escape(str(segment_id))}",
        )
    )
    console.print(Panel(escape(segment.source_content), title="Original"))
    if record.translation:
        console.print(Panel(escape(record.translation), title="Translation"))
    if record.error_message:
        console.print(Panel(escape(record.error_message), title="Error", subtitle_align="left"))
