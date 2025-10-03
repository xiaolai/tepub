from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rich.prompt import Confirm

from config import AppSettings

from .reader import EpubReader
from .resources import SpineItem, iter_spine_items


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


def _match_keyword(text: str, keywords: Iterable[str]) -> str | None:
    normalized = _normalize_text(text)
    for keyword in keywords:
        if keyword in normalized:
            return keyword
    return None


def _flatten_toc_entries(entries) -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []

    def _walk(items) -> None:
        for item in items:
            if item is None:
                continue
            href = getattr(item, "href", None)
            title = getattr(item, "title", "")
            if href and title:
                flattened.append((title, href))
            subitems = getattr(item, "subitems", None)
            if subitems:
                _walk(subitems)
            elif isinstance(item, (list, tuple)):
                _walk(item)

    _walk(entries)
    return flattened


def _collect_toc_candidates(
    spine_lookup: dict[Path, SpineItem],
    toc_entries: list[tuple[str, str]],
    keywords: Iterable[str],
) -> tuple[dict[Path, SkipCandidate], list[str]]:
    candidates: dict[Path, SkipCandidate] = {}
    unmatched_titles: list[str] = []
    total_entries = len(toc_entries)
    seen_titles: set[str] = set()

    for index, (title, href) in enumerate(toc_entries):
        href_path = Path(href.split("#", 1)[0])
        spine_item = spine_lookup.get(href_path)
        if spine_item is None:
            continue

        normalized_title = _normalize_text(title)
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


def _apply_skip_after_logic(
    candidates: dict[Path, SkipCandidate],
    spine_lookup: dict[Path, SpineItem],
    toc_entries: list[tuple[str, str]],
    settings: AppSettings,
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
    spine_lookup = {item.href: item for item in iter_spine_items(reader.book)}

    toc_entries = _flatten_toc_entries(getattr(reader.book, "toc", []))
    toc_candidates, unmatched_titles = _collect_toc_candidates(spine_lookup, toc_entries, keywords)

    # Only use TOC-based skip detection, not filename/content-based
    # Filenames are arbitrary technical artifacts and can cause false positives
    # (e.g., "index_split_000.html" is main content, not an index page)
    skip_candidates: dict[Path, SkipCandidate] = dict(toc_candidates)

    # Apply cascade skipping after back-matter triggers
    skip_candidates = _apply_skip_after_logic(skip_candidates, spine_lookup, toc_entries, settings)

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
