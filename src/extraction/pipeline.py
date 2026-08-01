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
from state.store import ensure_state, save_segments

console = get_console()

from .segments import iter_segments


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
