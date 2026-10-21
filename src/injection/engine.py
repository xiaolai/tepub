from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rich.markup import escape

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.titles import title_key
from epub_io.writer import write_updated_epub
from epub_io.xhtml import local_name, serialize_xhtml, text_of
from extraction.segments import locate_units
from logging_utils.logger import get_logger
from state.models import ExtractMode, Segment, SegmentStatus
from state.store import load_segments, load_state
from translation.languages import normalize_language
from translation.markup import strip_model_artifacts

from .html_ops import (
    ITEM_TAGS,
    _set_html_content,
    _set_language,
    _set_text_only,
    build_translation_element,
    inject_into_item,
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


HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _apply_translations_to_document(
    document,
    segments: list[tuple[Segment, str]],
    mode: str,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
    *,
    data_attributes: bool = True,
    source_language: str | None = None,
    target_language: str | None = None,
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
        # A model occasionally appends a ```html fence and an escaped copy of
        # the heading it translated. It is not part of the book, and in plain
        # text it is visible.
        translation = strip_model_artifacts(
            translation, markup=segment.extract_mode != ExtractMode.TEXT
        )
        if mode == "translated_only":
            source_title = (
                " ".join(text_of(original).split())
                if local_name(original) in HEADING_TAGS
                else None
            )
            _replace_with_translation(
                original, segment, translation, target_language=target_language
            )
            _record_heading_title(
                segment.file_path,
                original,
                title_updates,
                source_title=source_title,
            )
        elif local_name(original) in ITEM_TAGS:
            inject_into_item(
                original,
                segment,
                translation,
                data_attributes=data_attributes,
                source_language=source_language,
                target_language=target_language,
            )
        else:
            prepare_original(
                original,
                data_attributes=data_attributes,
                language=source_language,
            )
            insert_translation_after(
                original,
                build_translation_element(
                    original,
                    segment,
                    translation,
                    data_attributes=data_attributes,
                    language=target_language,
                ),
            )
    return bool(located), failed_ids


def _replace_with_translation(
    original,
    segment: Segment,
    translation: str,
    *,
    target_language: str | None = None,
) -> None:
    original.attrib.pop("data-lang", None)
    if segment.extract_mode == ExtractMode.TEXT:
        _set_text_only(original, translation)
    else:
        _set_html_content(original, translation)
    # The element keeps its attributes, source language included; the prose is
    # now in the target language, and a reader trusts lang over the document.
    _set_language(original, target_language)


def _record_heading_title(
    file_path: Path,
    element,
    title_updates: defaultdict[PurePosixPath, dict[str | None, str]],
    *,
    source_title: str | None = None,
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
    # The TOC's fragment is often an id on a section wrapper, not the heading
    # itself. Its label is then the only link between the entry and the
    # heading, so record that too rather than letting every unmatched entry
    # fall back to the file's first heading.
    text_key = title_key(source_title)
    if text_key:
        updates[text_key] = text
    updates.setdefault(None, text)


@dataclass(frozen=True)
class Injection:
    """The translated documents, and the units that could not be inserted.

    Failures were only printed, so an export missing translations, or a book
    written with none, ended as a success.
    """

    updated_html: dict[Path, bytes] = field(default_factory=dict)
    title_updates: dict[PurePosixPath, dict[str | None, str]] = field(default_factory=dict)
    # Units translated but not in the output: no longer matching the book, or
    # in a document missing from it or not well-formed.
    failed: tuple[str, ...] = ()
    # What the book is written in and what it was translated into, so the
    # writer can declare both in the package and on the translated elements.
    source_language: str | None = None
    target_language: str | None = None


def apply_translations(
    settings: AppSettings,
    input_epub: Path,
    *,
    mode: str | None = None,
) -> Injection:
    # Chinese typography is applied to the stored translations by the caller,
    # export, once for all editions and under the workspace lock it holds.
    settings.ensure_directories()

    console.print(f"[cyan]Preparing to inject translations into {escape(str(input_epub))}[/cyan]")

    grouped = _group_translated_segments(settings)
    if not grouped:
        console.print("[yellow]No translated segments found. Run translation first.[/yellow]")
        return Injection()

    total_segments = sum(len(segments) for segments in grouped.values())
    console.print(
        f"[cyan]Found {total_segments} translated segments across {len(grouped)} files.[/cyan]"
    )

    reader = EpubReader(input_epub, settings)
    documents = {doc.path: doc for doc in reader.iter_documents()}

    # The package's own language is the reliable source: settings say "auto"
    # for a book whose language was detected rather than chosen. The target is
    # what the translations were actually made in, which the state records;
    # the current settings may since have changed.
    source_language = (reader.package.metadata.language or "").strip() or None
    if not source_language or source_language.lower() == "auto":
        candidate, _ = normalize_language(settings.source_language)
        source_language = None if candidate == "auto" else candidate
    target_language, _ = normalize_language(load_state(settings.state_file).target_language)
    if not target_language or target_language == "auto":
        target_language = None

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
            failed_segments.extend(segment.segment_id for segment, _ in segments)
            continue
        if document.xhtml is None:
            # Not well-formed XML: copied into the output untranslated (D3).
            missing_documents.append(file_path)
            failed_segments.extend(segment.segment_id for segment, _ in segments)
            continue
        updated, failures = _apply_translations_to_document(
            document,
            segments,
            effective_mode,
            title_updates,
            # EPUB 2 content is XHTML 1.1, which has no data-* attributes.
            data_attributes=not reader.package.version.startswith("2"),
            source_language=source_language,
            target_language=target_language,
        )
        failed_segments.extend(failures)
        if updated:
            # Written back with its own XML declaration and DOCTYPE; the head was
            # never touched, so its title and stylesheet links are kept.
            updated_html[file_path] = serialize_xhtml(document.xhtml)

    if missing_documents:
        console.print(
            f"[yellow]Skipped {len(missing_documents)} missing documents: "
            f"{escape(', '.join(str(p) for p in missing_documents))}[/yellow]"
        )

    if failed_segments:
        console.print(
            f"[yellow]Encountered {len(failed_segments)} segment insertion failures; "
            "see logs for details.[/yellow]"
        )

    if effective_mode != "translated_only":
        title_updates.clear()

    return Injection(
        updated_html,
        {path: mapping for path, mapping in title_updates.items()},
        tuple(failed_segments),
        source_language=source_language,
        target_language=target_language,
    )


def run_injection(
    settings: AppSettings,
    input_epub: Path,
    output_epub: Path,
    *,
    mode: str = "bilingual",
    injection: Injection | None = None,
) -> Injection:
    """Write the translated EPUB; nothing is written when no unit could be inserted.

    ``injection`` is apply_translations' result for ``mode`` when the caller
    already has it, as export has when it also writes the web edition.
    """
    if injection is None:
        injection = apply_translations(settings, input_epub, mode=mode)
    updated_html, title_updates = injection.updated_html, injection.title_updates
    if not updated_html:
        return injection

    console.print(
        f"[cyan]Writing translated EPUB to {escape(str(output_epub))} "
        f"(mode={escape(str(mode))})...[/cyan]"
    )
    try:
        write_updated_epub(
            input_epub,
            output_epub,
            updated_html,
            toc_updates=title_updates,
            css_mode=mode,
            source_language=injection.source_language,
            target_language=injection.target_language,
        )
    except Exception as exc:  # pragma: no cover - filesystem errors
        console.print(f"[red]Failed to write EPUB: {escape(str(exc))}[/red]")
        raise
    else:
        console.print(
            f"[green]Wrote translated EPUB to {escape(str(output_epub))} with {len(updated_html)} "
            "updated files.[/green]"
        )
    return injection
