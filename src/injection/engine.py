from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.writer import write_updated_epub
from epub_io.xhtml import local_name, serialize_xhtml, text_of
from extraction.segments import locate_units
from logging_utils.logger import get_logger
from state.models import ExtractMode, Segment, SegmentStatus
from state.store import load_segments, load_state, save_state
from translation.polish import polish_if_chinese

from .html_ops import (
    _set_html_content,
    _set_text_only,
    build_translation_element,
    insert_translation_after,
    prepare_original,
)

logger = get_logger(__name__)
console = get_console()


def _group_translated_segments(settings: AppSettings) -> dict[Path, list[tuple[Segment, str]]]:
    segments_doc = load_segments(settings.segments_file)
    state_doc = load_state(settings.state_file)

    grouped: dict[Path, list[tuple[Segment, str]]] = defaultdict(list)
    for segment in segments_doc.segments:
        record = state_doc.segments.get(segment.segment_id)
        if not record or record.status != SegmentStatus.COMPLETED or not record.translation:
            continue
        # Skip auto-copied segments (those not translated by AI provider)
        if record.provider_name is None:
            continue
        grouped[segment.file_path].append((segment, record.translation))
    return grouped


HEADING_TAGS = {"h1", "h2", "h3", "h4"}


def _apply_translations_to_document(
    document,
    segments: list[tuple[Segment, str]],
    mode: str,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
) -> tuple[bool, list[str]]:
    """Inject translations, finding each unit by its place in the document.

    Every unit is located before anything changes, by running the same
    segmentation that produced the segments, and is trusted only if its source
    text still matches. Positional xpaths were stored instead, and broke on any
    parser change, on prefixed names such as epub:switch, and on edits.
    """
    units = locate_units(document.tree, document.path)
    located = []
    failed_ids: list[str] = []
    for segment, translation in segments:
        entry = units.get(segment.metadata.order_in_file)
        if entry is None or entry[2] != segment.source_content:
            logger.warning(
                "Segment %s no longer matches %s; it was not injected. Re-run extraction "
                "if the book changed.",
                segment.segment_id,
                document.path,
            )
            failed_ids.append(segment.segment_id)
            continue
        located.append((entry[0], segment, translation))

    for original, segment, translation in located:
        if mode == "translated_only":
            _replace_with_translation(original, segment, translation)
            _record_heading_title(segment.file_path, original, title_updates)
        else:
            prepare_original(original)
            insert_translation_after(
                original, build_translation_element(original, segment, translation)
            )
    return bool(located), failed_ids


def _replace_with_translation(original, segment: Segment, translation: str) -> None:
    original.attrib.pop("data-lang", None)
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(original, translation)
    else:
        _set_html_content(original, translation)


def _record_heading_title(
    file_path: Path,
    element,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
) -> None:
    if local_name(element) not in HEADING_TAGS:
        return
    text = " ".join(text_of(element).split())
    if not text:
        return
    key = PurePosixPath(file_path.as_posix())
    updates = title_updates[key]
    element_id = element.get("id")
    if element_id:
        updates[element_id] = text
    updates.setdefault(None, text)


def apply_translations(
    settings: AppSettings,
    input_epub: Path,
    *,
    mode: str | None = None,
) -> tuple[dict[Path, bytes], dict[PurePosixPath, dict[str | None, str]]]:
    settings.ensure_directories()

    polish_if_chinese(
        settings.state_file,
        settings.target_language,
        load_fn=load_state,
        save_fn=save_state,
        console_print=console.print,
        message_prefix="Before injection:",
    )

    console.print(f"[cyan]Preparing to inject translations into {input_epub}[/cyan]")

    grouped = _group_translated_segments(settings)
    if not grouped:
        console.print("[yellow]No translated segments found. Run translation first.[/yellow]")
        return {}, {}

    total_segments = sum(len(segments) for segments in grouped.values())
    console.print(
        f"[cyan]Found {total_segments} translated segments across {len(grouped)} files.[/cyan]"
    )

    reader = EpubReader(input_epub, settings)
    documents = {doc.path: doc for doc in reader.iter_documents()}

    updated_html: dict[Path, bytes] = {}
    effective_mode = mode or getattr(settings, "output_mode", "bilingual")
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]] = defaultdict(dict)
    failed_segments: list[str] = []
    missing_documents: list[Path] = []
    for file_path, segments in grouped.items():
        document = documents.get(file_path)
        if not document:
            logger.warning("Document %s missing from EPUB", file_path)
            missing_documents.append(file_path)
            continue
        if document.xhtml is None:
            # Not well-formed XML: copied into the output untranslated (D3).
            missing_documents.append(file_path)
            continue
        updated, failures = _apply_translations_to_document(
            document, segments, effective_mode, title_updates
        )
        failed_segments.extend(failures)
        if updated:
            # Written back with its own XML declaration and DOCTYPE; the head was
            # never touched, so its title and stylesheet links are kept.
            updated_html[file_path] = serialize_xhtml(document.xhtml)

    if missing_documents:
        console.print(
            f"[yellow]Skipped {len(missing_documents)} missing documents: {', '.join(str(p) for p in missing_documents)}[/yellow]"
        )

    if failed_segments:
        console.print(
            f"[yellow]Encountered {len(failed_segments)} segment insertion failures; see logs for details.[/yellow]"
        )

    if effective_mode != "translated_only":
        title_updates.clear()

    return updated_html, {path: mapping for path, mapping in title_updates.items()}


def run_injection(
    settings: AppSettings,
    input_epub: Path,
    output_epub: Path,
    *,
    mode: str = "bilingual",
) -> tuple[dict[Path, bytes], dict[PurePosixPath, dict[str | None, str]]]:
    updated_html, title_updates = apply_translations(settings, input_epub, mode=mode)
    if not updated_html:
        return updated_html, title_updates

    console.print(f"[cyan]Writing translated EPUB to {output_epub} (mode={mode})...[/cyan]")
    try:
        write_updated_epub(
            input_epub,
            output_epub,
            updated_html,
            toc_updates=title_updates,
            css_mode=mode,
        )
    except Exception as exc:  # pragma: no cover - filesystem errors
        console.print(f"[red]Failed to write EPUB: {exc}[/red]")
        raise
    else:
        console.print(
            f"[green]Wrote translated EPUB to {output_epub} with {len(updated_html)} updated files.[/green]"
        )
    return updated_html, title_updates
