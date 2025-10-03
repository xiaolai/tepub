from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path, PurePosixPath

from lxml import etree, html

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.writer import write_updated_epub
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


def _restore_document_structure(document, raw_html: bytes) -> None:
    try:
        original_root = html.fromstring(raw_html)
    except Exception:  # pragma: no cover - malformed markup fallback
        return

    new_root = document.tree

    original_head = original_root.find("head")
    new_head = new_root.find("head")
    if original_head is not None and new_head is not None:
        new_head.attrib.clear()
        new_head.attrib.update(original_head.attrib)
        new_head[:] = [deepcopy(child) for child in original_head]

    original_body = original_root.find("body")
    new_body = new_root.find("body")
    if original_body is not None and new_body is not None:
        new_body.attrib.clear()
        new_body.attrib.update(original_body.attrib)


def _apply_translations_to_document(
    document,
    segments: list[tuple[Segment, str]],
    mode: str,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
) -> tuple[bool, list[str]]:
    updated = False
    failed_ids: list[str] = []
    tree = document.tree
    root_tree = tree.getroottree()
    for segment, translation in sorted(
        segments,
        key=lambda item: item[0].metadata.order_in_file,
        reverse=True,
    ):
        nodes = root_tree.xpath(segment.xpath)
        if not nodes:
            logger.warning("XPath not found for segment %s", segment.segment_id)
            failed_ids.append(segment.segment_id)
            continue
        original = nodes[0]
        if mode == "translated_only":
            _replace_with_translation(original, segment, translation)
            _record_heading_title(segment.file_path, original, title_updates)
        else:
            prepare_original(original)
            translation_element = build_translation_element(original, segment, translation)
            try:
                insert_translation_after(original, translation_element)
            except ValueError as exc:
                logger.warning("Failed to insert translation for %s: %s", segment.segment_id, exc)
                failed_ids.append(segment.segment_id)
                continue
        updated = True
    return updated, failed_ids


def _replace_with_translation(
    original: html.HtmlElement, segment: Segment, translation: str
) -> None:
    original.attrib.pop("data-lang", None)
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(original, translation)
    else:
        _set_html_content(original, translation)


def _record_heading_title(
    file_path: Path,
    element: html.HtmlElement,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
) -> None:
    tag = (element.tag or "").lower()
    if tag not in HEADING_TAGS:
        return
    text = element.text_content().strip()
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
        updated, failures = _apply_translations_to_document(
            document, segments, effective_mode, title_updates
        )
        failed_segments.extend(failures)
        if updated:
            html_bytes = etree.tostring(document.tree, encoding="utf-8", method="html")
            updated_html[file_path] = html_bytes

    if missing_documents:
        console.print(
            f"[yellow]Skipped {len(missing_documents)} missing documents: {', '.join(str(p) for p in missing_documents)}[/yellow]"
        )

    if failed_segments:
        console.print(
            f"[yellow]Encountered {len(failed_segments)} segment insertion failures; see logs for details.[/yellow]"
        )

    _restore_document_structure(document, document.raw_html)

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
