"""How an audiobook is divided into chapters, and when a built chapter is current.

Split out of assembly.py, which did six jobs in one module.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.xhtml import document_title
from state.models import Segment

logger = logging.getLogger(__name__)
console = get_console()



def _slugify(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_\-]", "", value)
    return value or "audiobook"

def _document_titles(reader: EpubReader) -> dict[str, str]:
    titles: dict[str, str] = {}
    for document in reader.iter_documents():
        tree = document.tree
        if tree is None:
            continue
        titles[document.path.as_posix()] = document_title(tree) or document.path.stem
    return titles

def _chapter_title(
    file_path: str,
    toc_map: dict[str, str],
    doc_titles: dict[str, str],
    custom_map: dict[str, str] | None = None,
) -> str:
    # Priority: custom > TOC > document title > filename
    if custom_map and file_path in custom_map:
        return custom_map[file_path]
    title = toc_map.get(file_path)
    if title:
        return title
    return doc_titles.get(file_path, Path(file_path).stem)

def _build_spine_to_toc_map(
    reader: EpubReader, toc_map: dict[str, str]
) -> dict[int, tuple[str, str]]:
    """Build mapping from spine index to (toc_file, toc_title).

    Maps each spine item to its governing TOC entry. Files between TOC entries,
    and after the last one, are mapped to the previous TOC entry. Files before
    the first TOC entry each map to themselves, as chapters of their own.

    Returns:
        Dict mapping spine_index -> (toc_file_path, toc_title)
    """
    # Build spine index lookup
    spine_lookup: dict[str, int] = {
        item.href.as_posix(): item.index for item in reader.package.spine_items()
    }

    # Find spine indices for TOC entries
    toc_entries: list[tuple[int, str, str]] = []  # (spine_index, file_path, title)
    for file_path, title in toc_map.items():
        spine_idx = spine_lookup.get(file_path)
        if spine_idx is not None:
            toc_entries.append((spine_idx, file_path, title))

    if not toc_entries:
        # No TOC entries found, return empty map
        return {}

    # Sort by spine index
    toc_entries.sort(key=lambda x: x[0])

    # Build the mapping: spine_index -> (toc_file, toc_title)
    result: dict[int, tuple[str, str]] = {}

    first_toc_idx = toc_entries[0][0]
    hrefs = {item.index: item.href.as_posix() for item in reader.package.spine_items()}

    # Map spine indices to their governing TOC entry. Documents before the first
    # entry each become a chapter of their own, and documents after the last
    # belong to it; both used to be synthesised and then left out of the book.
    current_toc_idx = 0
    for spine_idx in range(len(reader.package.spine)):
        if spine_idx < first_toc_idx:
            if spine_idx in hrefs:
                result[spine_idx] = (hrefs[spine_idx], "")
            continue

        # Find the appropriate TOC entry for this spine index
        while (
            current_toc_idx < len(toc_entries) - 1
            and spine_idx >= toc_entries[current_toc_idx + 1][0]
        ):
            current_toc_idx += 1

        toc_spine_idx, toc_file, toc_title = toc_entries[current_toc_idx]
        result[spine_idx] = (toc_file, toc_title)

    return result

def group_segments_into_chapters(
    segments, spine_to_toc: dict
) -> list[tuple[str, list]]:
    """Group segments into chapters by TOC entry, falling back to file grouping.

    Shared by final assembly and the preview chapter export. They previously
    carried independent copies of this grouping and sort, so a preview could
    disagree with the book it was previewing.
    """
    chapter_map: dict[str, list] = {}
    for segment in segments:
        if spine_to_toc:
            toc_entry = spine_to_toc.get(segment.metadata.spine_index)
            # Every spine document is mapped (see _build_spine_to_toc_map); a
            # segment outside the spine would be a different book.
            key = toc_entry[0] if toc_entry is not None else segment.file_path.as_posix()
        else:
            key = segment.file_path.as_posix()
        chapter_map.setdefault(key, []).append(segment)

    return sorted(
        chapter_map.items(),
        key=lambda item: (
            min(seg.metadata.spine_index for seg in item[1]),
            min(seg.metadata.order_in_file for seg in item[1]),
        ),
    )

def _load_custom_chapter_titles(settings: AppSettings) -> dict[str, str]:
    """Map segment file path -> custom chapter title from chapters.yaml, if present."""
    custom_chapters_map: dict[str, str] = {}
    chapters_yaml_path = settings.work_dir / "chapters.yaml"
    if not chapters_yaml_path.exists():
        return custom_chapters_map

    try:
        from .chapters import read_chapters_yaml

        chapters, _metadata = read_chapters_yaml(chapters_yaml_path)
        console.print("[cyan]Loading custom chapter titles from chapters.yaml[/cyan]")
        # Every segment file in a chapter inherits that chapter's title.
        for chapter in chapters:
            for seg_file in chapter.segments:
                custom_chapters_map[seg_file] = chapter.title
    except Exception as exc:
        logger.warning(f"Failed to load chapters.yaml: {exc}")
        console.print(f"[yellow]Warning: Could not load chapters.yaml: {exc}[/yellow]")

    return custom_chapters_map

def chapter_is_current(chapter_path: Path, segments: list[Segment], audio_state) -> bool:
    """True when a cached chapter is newer than, and built from, these segments.

    Existence alone is not enough: re-synthesising with a different voice,
    speed or source text rewrites the segment audio but leaves the chapter
    file in place, so the old audio was served indefinitely.

    Timestamps alone are not enough either. If a chapter was built from
    segments A+B and a later run includes only A, every remaining source file
    can be older than the chapter, so the stale chapter — still containing B —
    was reused. The composition is recorded alongside the audio and compared.
    """
    if not chapter_path.exists():
        return False
    manifest_path = chapter_path.with_suffix(chapter_path.suffix + ".segments")
    expected = "\n".join(segment.segment_id for segment in segments)
    try:
        if manifest_path.read_text(encoding="utf-8") != expected:
            return False
    except OSError:
        # No manifest: written by an older version, composition unknown.
        return False
    chapter_mtime = chapter_path.stat().st_mtime
    for segment in segments:
        seg_state = audio_state.segments.get(segment.segment_id)
        if not seg_state or not seg_state.audio_path:
            continue
        source = Path(seg_state.audio_path)
        if source.exists() and source.stat().st_mtime > chapter_mtime:
            return False
    return True
