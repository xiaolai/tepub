from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape
from rich.progress import Progress

from config import AppSettings
from config.workspace import epub_digest
from console_singleton import get_console, live_display_enabled
from epub_io.container import metadata_summary
from epub_io.reader import EpubReader
from epub_io.selector import build_skip_map
from state.base import safe_load_state
from state.models import (
    Segment,
    SegmentsDocument,
    SegmentStatus,
    SkippedDocument,
    TranslationRecord,
)
from state.store import backup_state, ensure_state, load_state, save_segments, save_state
from state.writer import exclusive_run
from translation.languages import normalize_language

from .migrate import SEGMENTS_FORMAT, finish_legacy_import, import_legacy_workspace
from .segments import iter_segments, loose_body_text

console = get_console()


def run_extraction(settings: AppSettings, input_epub: Path) -> None:
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    reader = EpubReader(input_epub, settings)
    segments, skipped_documents = _extract_units(reader, settings)
    _check_unique_ids(segments)
    _save_workspace(settings, segments, _segments_document(reader, segments, skipped_documents))


def _extract_units(
    reader: EpubReader, settings: AppSettings
) -> tuple[list[Segment], list[SkippedDocument]]:
    """Every unit of the book's linear spine documents, in order, tagged with
    its document's skip decision, and the documents flagged as skipped."""
    skip_map = build_skip_map(reader.epub_path, settings, interactive=False, reader=reader)
    skipped_documents: list[SkippedDocument] = []
    segments: list[Segment] = []
    with Progress(console=console, disable=not live_display_enabled(console)) as progress:
        task = progress.add_task("Extracting", total=None)
        nav = reader.package.nav_item()
        nav_href = reader.package.package_href(nav.path) if nav is not None else None
        # documents(): those the skip analysis already parsed are not parsed again.
        for document in reader.documents():
            file_path = document.path
            if not document.spine_item.linear:
                continue
            if file_path.as_posix() == nav_href:
                # The navigation document's titles are translated through the
                # table-of-contents update. Extracted as a chapter, bilingual output
                # put a second heading and list inside <nav>, which is invalid.
                continue
            if document.tree is None:
                # Not well-formed XML: left untranslated, already warned (D3).
                continue
            _warn_of_loose_text(document.tree, file_path)
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
    return segments, skipped_documents


def _warn_of_loose_text(tree, file_path: Path) -> None:
    loose = loose_body_text(tree)
    if loose:
        console.print(
            f"[yellow]{escape(file_path.as_posix())}: {loose} passage(s) of text sit "
            "directly in <body>, outside any paragraph, and will not be "
            "translated.[/yellow]"
        )


def _check_unique_ids(segments: list[Segment]) -> None:
    # Unit ids hash the full EPUB path with the unit's place in the document, so
    # they cannot collide; a duplicate here would be an extraction defect.
    ids = [segment.segment_id for segment in segments]
    if len(ids) != len(set(ids)):
        duplicates = sorted(sid for sid, count in Counter(ids).items() if count > 1)
        raise RuntimeError(f"Extraction produced duplicate segment ids: {duplicates[:5]}")


def _segments_document(
    reader: EpubReader, segments: list[Segment], skipped_documents: list[SkippedDocument]
) -> SegmentsDocument:
    metadata = metadata_summary(reader.package.metadata)
    return SegmentsDocument(
        format_version=SEGMENTS_FORMAT,
        # Absolute: status finds the book from it in any working directory.
        epub_path=reader.epub_path.resolve(),
        epub_sha256=epub_digest(reader.epub_path),
        generated_at=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        skipped_documents=skipped_documents,
        book_title=metadata.get("title"),
        book_author=metadata.get("author"),
        book_publisher=metadata.get("publisher"),
        book_year=metadata.get("year"),
    )


def _save_workspace(
    settings: AppSettings, segments: list[Segment], segments_doc: SegmentsDocument
) -> None:
    """Bring the state in line with the new units, then save them."""
    segments_path = settings.segments_file
    state_path = settings.state_file
    segments_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Held like a translate run holds it: extraction rewrites the state, and
    # unlocked it could overwrite translations a running translate had saved.
    with exclusive_run(state_path):
        # What an undigested record can be checked against: the previous
        # extraction's text, or a migration that matched it by text already.
        previous_segments: list[Segment] = []
        migrated = False
        if segments_path.exists():
            previous = safe_load_state(segments_path, SegmentsDocument, "segments")
            if previous.format_version < SEGMENTS_FORMAT:
                _import_legacy(settings, previous, segments)
                migrated = True
            else:
                previous_segments = previous.segments

        # ensure_state merges: existing translations are kept and only newly
        # extracted segments are added. Writing build_default_state()
        # unconditionally meant re-running `tepub extract` on a partially
        # translated book silently discarded every completed translation.
        ensure_state(
            state_path,
            segments,
            provider=settings.primary_provider.name,
            model=settings.primary_provider.model,
            # Codes, as a translate run records them; see ensure_state.
            source_language=normalize_language(settings.source_language)[0],
            target_language=normalize_language(settings.target_language)[0],
        )
        _reconcile_sources(state_path, previous_segments, segments, migrated=migrated)
        # Saved last: the state is reconciled against the previous segments,
        # so a failure before this point leaves both to be compared again.
        save_segments(segments_doc, segments_path)
        # Only now is a format migration final; until then a retry redoes it
        # from the files as they were before it.
        finish_legacy_import(settings.work_dir)


def _import_legacy(
    settings: AppSettings, previous: SegmentsDocument, segments: list[Segment]
) -> None:
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


def source_digest(segment: Segment) -> str:
    text = f"{segment.extract_mode.value}\0{segment.source_content}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reconcile_sources(
    state_path: Path, previous: list[Segment], current: list[Segment], *, migrated: bool
) -> None:
    """Reset records whose unit's source changed, and stamp every record with
    its unit's source digest.

    Unit ids come from a unit's file and place, not its text, so an edited
    book, or another book in the same workspace, kept the old translation of
    whatever paragraph used to sit there and exported it as this one's.
    Records of units no longer extracted are kept: a changed skip rule
    leaves units out, and dropping their finished translations would mean
    paying for them again. Their digest still catches a unit that comes back
    changed. A record with no digest, from before digests were kept, is
    compared with the previous extraction's text, or trusted after a migration,
    which matched it by text. With neither, nothing says the translation is of
    this text: a state left without its segments, perhaps another book's, so a
    finished one is reset, with a copy kept.
    """
    before = {s.segment_id: source_digest(s) for s in previous}
    state = load_state(state_path)
    stale: list[str] = []
    stamped = False
    for segment in current:
        record = state.segments.get(segment.segment_id)
        if record is None:
            continue
        digest = source_digest(segment)
        known = record.source_sha256 or before.get(segment.segment_id)
        unverifiable = (
            known is None and not migrated and record.status == SegmentStatus.COMPLETED
        )
        if (known is not None and known != digest) or unverifiable:
            stale.append(segment.segment_id)
        elif record.source_sha256 != digest:
            record.source_sha256 = digest
            stamped = True
    if not stale and not stamped:
        return
    finished = sum(1 for sid in stale if state.segments[sid].status == SegmentStatus.COMPLETED)
    backup = backup_state(state_path) if finished else None
    now = {s.segment_id: s for s in current}
    for segment_id in stale:
        state.segments[segment_id] = TranslationRecord(
            segment_id=segment_id, source_sha256=source_digest(now[segment_id])
        )
    save_state(state, state_path)
    if backup is not None:
        console.print(
            f"[yellow]{finished} translated units do not match this book's text, or "
            f"cannot be checked against it, and will be translated again. The previous "
            f"state is saved as {escape(backup.name)}.[/yellow]"
        )
