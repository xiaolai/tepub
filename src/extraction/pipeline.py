from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.progress import Progress

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.resources import extract_metadata
from epub_io.selector import build_skip_map
from state.models import Segment, SegmentsDocument, SkippedDocument, build_default_state
from state.store import ensure_state, load_state, save_segments, save_state

console = get_console()

from .segments import iter_segments


def _audit_extraction(settings: AppSettings, input_epub: Path, segments: list[Segment]) -> None:
    console.print("[cyan]Let me double-check the extraction output…[/cyan]")

    total_segments = len(segments)
    unique_ids = {seg.segment_id for seg in segments}
    if len(unique_ids) != total_segments:
        console.print(
            f"[yellow]Warning: Found duplicate segment IDs. Total {total_segments}, unique {len(unique_ids)}.[/yellow]"
        )

    state = load_state(settings.state_file)
    missing = [seg.segment_id for seg in segments if seg.segment_id not in state.segments]
    extra = [seg_id for seg_id in state.segments if seg_id not in unique_ids]

    if missing or extra:
        console.print(
            "[yellow]Some segment statuses are out of sync; mending those omissions or errors…[/yellow]"
        )
        ensure_state(
            settings.state_file,
            segments,
            provider=settings.primary_provider.name,
            model=settings.primary_provider.model,
            source_language=state.source_language,
            target_language=state.target_language,
            force_reset=True,
        )
        state = load_state(settings.state_file)
        missing = [seg.segment_id for seg in segments if seg.segment_id not in state.segments]
        extra = [seg_id for seg_id in state.segments if seg_id not in unique_ids]
        if not missing and not extra:
            console.print("[green]Audit mended the state file successfully.[/green]")
        else:
            console.print(
                "[red]Audit still found mismatches after repair. Consider re-running extraction.[/red]"
            )

    console.print(
        f"[cyan]Segments total: {total_segments}; unique IDs: {len(unique_ids)}; state entries: {len(state.segments)}.[/cyan]"
    )


def run_extraction(settings: AppSettings, input_epub: Path) -> None:
    work_dir = settings.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    reader = EpubReader(input_epub, settings)
    skip_map = build_skip_map(input_epub, settings, interactive=False)
    skipped_documents: list[SkippedDocument] = []

    segments: list[Segment] = []
    with Progress() as progress:
        task = progress.add_task("Extracting", total=None)
        for document in reader.iter_documents():
            file_path = document.path
            if not document.spine_item.linear:
                continue
            decision = skip_map.get(file_path)
            skip_reason = None
            skip_source = None
            if decision and decision.flagged:
                # Track skipped files for reporting, but still extract segments
                skipped_documents.append(
                    SkippedDocument(
                        file_path=file_path,
                        reason=decision.reason,
                        source=decision.source,
                    )
                )
                skip_reason = decision.reason
                skip_source = decision.source
            for segment in iter_segments(
                document.tree, file_path=file_path, spine_index=document.spine_item.index
            ):
                # Tag segments with skip metadata if file is flagged
                segment.skip_reason = skip_reason
                segment.skip_source = skip_source
                segments.append(segment)
                progress.advance(task)

    # Extract book metadata
    metadata = extract_metadata(reader.book)

    timestamp = datetime.now(timezone.utc).isoformat()
    segments_doc = SegmentsDocument(
        epub_path=input_epub,
        generated_at=timestamp,
        segments=segments,
        skipped_documents=skipped_documents,
        book_title=metadata.get("title"),
        book_author=metadata.get("author"),
        book_publisher=metadata.get("publisher"),
        book_year=metadata.get("year"),
    )
    segments_path = settings.segments_file
    segments_path.parent.mkdir(parents=True, exist_ok=True)
    save_segments(segments_doc, segments_path)

    state_path = settings.state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_doc = build_default_state(
        segments,
        provider=settings.primary_provider.name,
        model=settings.primary_provider.model,
    )
    save_state(state_doc, state_path)
