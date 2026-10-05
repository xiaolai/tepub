from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rich.prompt import Confirm

from config import AppSettings

from .container import SpineDocument as SpineItem
from .container import iter_toc
from .reader import EpubReader


@dataclass
class SkipAnalysis:
    candidates: list[SkipCandidate]
    toc_unmatched_titles: list[str]


@dataclass
class SkipCandidate:
    file_path: Path
    spine_index: int
    reason: str
    source: str = "content"
    flagged: bool = True


TOC_FRONT_SAMPLE = 8
TOC_BACK_SAMPLE = 6


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    """Word-boundary matcher for an ASCII keyword, or None for scripts without them."""
    if not keyword.isascii():
        # CJK and similar scripts have no word boundaries; substring is the only
        # workable test there.
        return None
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)")


def _match_keyword(text: str, keywords: Iterable[str]) -> str | None:
    normalized = _normalize_text(text)
    for keyword in keywords:
        # Raw substring matching made "cover" match "discover", "index" match
        # "indexation" and "notes" match "footnotes", so ordinary chapters were
        # skipped from translation.
        pattern = _keyword_pattern(keyword)
        if pattern is not None:
            if pattern.search(normalized):
                return keyword
        elif keyword in normalized:
            return keyword
    return None


def _flatten_toc_entries(entries) -> list[tuple[str, str]]:
    """(title, href) for every titled, targeted entry, in reading order."""
    return [(entry.title, entry.href) for entry in iter_toc(entries) if entry.href and entry.title]


def _collect_toc_candidates(
    spine_lookup: dict[Path, SpineItem],
    toc_entries: list[tuple[str, str]],
    keywords: Iterable[str],
    back_matter: Iterable[str] = (),
    back_matter_from: int | None = None,
) -> tuple[dict[Path, SkipCandidate], list[str]]:
    """Files to skip by their TOC titles, and titles no rule matched.

    A file is named by its first TOC entry; later entries are its sections,
    and one titled "Further Reading" skipped a whole chapter. A later entry
    still marks its file when it is back matter (``back_matter``) in the last
    part of the TOC (from ``back_matter_from``): "Technical Terms" followed by
    "Index" in one file at the end of a book is back matter throughout.
    """
    candidates: dict[Path, SkipCandidate] = {}
    unmatched_titles: list[str] = []
    total_entries = len(toc_entries)
    seen_titles: set[str] = set()
    decided: set[Path] = set()

    for index, (title, href) in enumerate(toc_entries):
        href_path = Path(href.split("#", 1)[0])
        spine_item = spine_lookup.get(href_path)
        if spine_item is None:
            continue

        normalized_title = _normalize_text(title)
        if href_path in decided:
            if href_path not in candidates and back_matter_from is not None and index >= back_matter_from:
                keyword = _match_keyword(normalized_title, back_matter)
                if keyword:
                    candidates[href_path] = SkipCandidate(
                        file_path=href_path,
                        spine_index=spine_item.index,
                        reason=keyword,
                        source="toc",
                    )
            continue
        decided.add(href_path)
        keyword = _match_keyword(normalized_title, keywords)
        if keyword:
            candidates[href_path] = SkipCandidate(
                file_path=href_path,
                spine_index=spine_item.index,
                reason=keyword,
                source="toc",
            )
        else:
            if (
                normalized_title
                and normalized_title not in seen_titles
                and not any(ch.isdigit() for ch in normalized_title)
                and "chapter" not in normalized_title
                and "part" not in normalized_title
                and (index < TOC_FRONT_SAMPLE or index >= max(total_entries - TOC_BACK_SAMPLE, 0))
            ):
                unmatched_titles.append(normalized_title)
                seen_titles.add(normalized_title)
    return candidates, unmatched_titles


def first_line(tree) -> str:
    """A document's first line of text: its first heading or paragraph."""
    from .xhtml import local_name, text_of

    body = next((e for e in tree.iter() if isinstance(e.tag, str) and local_name(e) == "body"), None)
    for element in (body if body is not None else tree).iter():
        if isinstance(element.tag, str) and local_name(element) in (
            "h1", "h2", "h3", "h4", "h5", "h6", "p"
        ):
            text = _normalize_text(text_of(element))
            if text:
                return text
    return ""


def _untitled_back_matter(
    spine_lookup: dict[Path, SpineItem],
    toc_paths: set[Path],
    after_index: int,
    triggers: Iterable[str],
    first_line_of: Callable[[Path], str],
) -> tuple[Path, str] | None:
    """The first document the TOC leaves out whose first line is a back-matter
    title and nothing else, such as "Notes", past ``after_index`` in the spine.

    Converted books often list only the chapters: one had six files of notes,
    their title a paragraph reading "Notes", that no TOC rule could see.
    Requiring the whole first line to be the title keeps a chapter that
    merely starts with the word from being taken for one.
    """
    for path, item in sorted(spine_lookup.items(), key=lambda pair: pair[1].index):
        if item.index <= after_index or path in toc_paths:
            continue
        line = first_line_of(path)
        keyword = _match_keyword(line, triggers)
        if keyword and _keyword_pattern(keyword) is not None and _keyword_pattern(keyword).fullmatch(line):
            return path, keyword
    return None


def _apply_skip_after_logic(
    candidates: dict[Path, SkipCandidate],
    spine_lookup: dict[Path, SpineItem],
    toc_entries: list[tuple[str, str]],
    settings: AppSettings,
    first_line_of: Callable[[Path], str] | None = None,
) -> dict[Path, SkipCandidate]:
    """
    Apply cascade skipping after back-matter triggers.

    When a back-matter section (index, notes, bibliography, etc.) is found
    in the last portion of the TOC, skip all subsequent spine items.

    This prevents processing hundreds of continuation pages for indexes
    and endnotes that are split across many HTML files.

    Args:
        candidates: Existing skip candidates from TOC matching
        spine_lookup: Map of href to SpineItem for all spine items
        toc_entries: Flattened list of (title, href) from TOC
        settings: App settings with cascade skip configuration

    Returns:
        Updated candidates dictionary with cascade skip entries added
    """
    if not settings.skip_after_back_matter:
        return candidates

    total_entries = len(toc_entries)
    threshold_index = int(total_entries * settings.back_matter_threshold)
    trigger_spine_index = None
    trigger_keyword = None

    # Find earliest back-matter trigger in last portion of TOC
    for index, (title, href) in enumerate(toc_entries):
        if index < threshold_index:
            continue

        normalized_title = _normalize_text(title)
        keyword = _match_keyword(normalized_title, settings.back_matter_triggers)
        if keyword:
            href_path = Path(href.split("#", 1)[0])
            spine_item = spine_lookup.get(href_path)
            if spine_item:
                trigger_spine_index = spine_item.index
                trigger_keyword = keyword
                break

    # Back matter the TOC leaves out, found by its first line; it triggers the
    # cascade if it comes before any trigger the TOC has.
    if first_line_of is not None and toc_entries:
        toc_paths = {Path(href.split("#", 1)[0]) for _, href in toc_entries}
        threshold_href = Path(toc_entries[min(threshold_index, total_entries - 1)][1].split("#", 1)[0])
        threshold_item = spine_lookup.get(threshold_href)
        untitled = _untitled_back_matter(
            spine_lookup,
            toc_paths,
            threshold_item.index if threshold_item else -1,
            settings.back_matter_triggers,
            first_line_of,
        )
        if untitled is not None:
            path, keyword = untitled
            if trigger_spine_index is None or spine_lookup[path].index < trigger_spine_index:
                trigger_spine_index, trigger_keyword = spine_lookup[path].index, keyword
                candidates.setdefault(
                    path,
                    SkipCandidate(
                        file_path=path,
                        spine_index=spine_lookup[path].index,
                        reason=keyword,
                        source="content",
                    ),
                )

    # If trigger found, mark all subsequent spine items for cascade skipping
    if trigger_spine_index is not None:
        for path, item in spine_lookup.items():
            if item.index > trigger_spine_index and path not in candidates:
                candidates[path] = SkipCandidate(
                    file_path=path,
                    spine_index=item.index,
                    reason=f"after {trigger_keyword}",
                    source="cascade",
                )

    return candidates


def analyze_skip_candidates(epub_path: Path, settings: AppSettings) -> SkipAnalysis:
    reader = EpubReader(epub_path, settings)
    keywords = [rule.keyword for rule in settings.skip_rules]
    spine_lookup = {item.href: item for item in reader.package.spine_items()}

    toc_entries = _flatten_toc_entries(reader.package.toc)
    toc_candidates, unmatched_titles = _collect_toc_candidates(
        spine_lookup,
        toc_entries,
        keywords,
        back_matter=settings.back_matter_triggers if settings.skip_after_back_matter else (),
        back_matter_from=int(len(toc_entries) * settings.back_matter_threshold),
    )

    # Only use TOC-based skip detection, not filename/content-based
    # Filenames are arbitrary technical artifacts and can cause false positives
    # (e.g., "index_split_000.html" is main content, not an index page)
    skip_candidates: dict[Path, SkipCandidate] = dict(toc_candidates)

    # Apply cascade skipping after back-matter triggers
    def first_line_of(path: Path) -> str:
        tree = reader.read_document_by_path(path).tree
        return first_line(tree) if tree is not None else ""

    skip_candidates = _apply_skip_after_logic(
        skip_candidates, spine_lookup, toc_entries, settings, first_line_of
    )

    ordered_candidates = sorted(
        skip_candidates.values(), key=lambda c: (c.spine_index, c.file_path.as_posix())
    )
    return SkipAnalysis(candidates=ordered_candidates, toc_unmatched_titles=unmatched_titles)


def collect_skip_candidates(epub_path: Path, settings: AppSettings) -> list[SkipCandidate]:
    analysis = analyze_skip_candidates(epub_path, settings)
    return analysis.candidates


def build_skip_map(
    epub_path: Path, settings: AppSettings, *, interactive: bool = False
) -> dict[Path, SkipCandidate]:
    candidates = collect_skip_candidates(epub_path, settings)
    skip_map: dict[Path, SkipCandidate] = {}
    for candidate in candidates:
        skip = candidate.flagged
        if interactive:
            skip = Confirm.ask(
                f"Skip {candidate.file_path.as_posix()}? reason={candidate.reason}",
                default=True,
            )
        candidate.flagged = skip
        skip_map[candidate.file_path] = candidate
    return skip_map
