from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.progress import Progress

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.resources import extract_metadata
from epub_io.selector import build_skip_map
from state.models import Segment, SegmentsDocument, SkippedDocument
from state.store import ensure_state, load_segments, save_segments

console = get_console()

from .migrate import SEGMENTS_FORMAT, import_legacy_workspace
from .segments import iter_segments, loose_body_text


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
            if document.tree is None:
                # Not well-formed XML: left untranslated, already warned (D3).
                continue
            loose = loose_body_text(document.tree)
            if loose:
                console.print(
                    f"[yellow]{file_path.as_posix()}: {loose} passage(s) of text sit "
                    "directly in <body>, outside any paragraph, and will not be "
                    "translated.[/yellow]"
                )
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
    # Unit ids hash the full EPUB path with the unit's place in the document, so
    # they cannot collide; a duplicate here would be an extraction defect.
    ids = [segment.segment_id for segment in segments]
    if len(ids) != len(set(ids)):
        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        raise RuntimeError(f"Extraction produced duplicate segment ids: {duplicates[:5]}")

    segments_path = settings.segments_file
    if segments_path.exists():
        previous = load_segments(segments_path)
        if previous.format_version < SEGMENTS_FORMAT:
            report = import_legacy_workspace(
                settings.work_dir, settings.state_file, previous.segments, segments
            )
            console.print(
                f"[cyan]Updated this workspace to the new segmentation: {report.mapped} of "
                f"{len(previous.segments)} earlier segments matched a new unit by file and "
                f"text; {report.audio_files_remapped} audio clips kept.[/cyan]"
            )
            if report.finished_not_carried:
                console.print(
                    f"[yellow]{report.finished_not_carried} finished translations matched no "
                    "new unit and will be translated again.[/yellow]"
                )
            if report.backups:
                console.print(
                    "[dim]Previous state saved as: "
                    + ", ".join(path.name for path in report.backups)
                    + "[/dim]"
                )

    segments_doc = SegmentsDocument(
        format_version=SEGMENTS_FORMAT,
        epub_path=input_epub,
        generated_at=timestamp,
        segments=segments,
        skipped_documents=skipped_documents,
        book_title=metadata.get("title"),
        book_author=metadata.get("author"),
        book_publisher=metadata.get("publisher"),
        book_year=metadata.get("year"),
    )
    segments_path.parent.mkdir(parents=True, exist_ok=True)
    save_segments(segments_doc, segments_path)

    state_path = settings.state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_state merges: existing translations are kept and only newly extracted
    # segments are added. Writing build_default_state() unconditionally meant
    # re-running `tepub extract` on a partially translated book silently discarded
    # every completed translation.
    ensure_state(
        state_path,
        segments,
        provider=settings.primary_provider.name,
        model=settings.primary_provider.model,
        source_language=settings.source_language,
        target_language=settings.target_language,
    )
