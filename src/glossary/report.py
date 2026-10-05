"""Which finished translations do not follow the glossary."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from state.models import Segment, SegmentStatus, StateDocument

from .model import Glossary


@dataclass(frozen=True)
class Miss:
    segment_id: str
    term: str
    problem: str


def find_misses(segments: Iterable[Segment], state: StateDocument, glossary: Glossary) -> list[Miss]:
    """Each glossary problem in a completed, model-translated unit.

    Units copied without translation (punctuation, numbers) are not checked.
    """
    misses = []
    for segment in segments:
        record = state.segments.get(segment.segment_id)
        if (
            record is None
            or record.status != SegmentStatus.COMPLETED
            or not record.translation
            or record.provider_name is None
        ):
            continue
        for term in glossary.terms_in(segment.source_content):
            for problem in glossary.problems([term], record.translation):
                misses.append(Miss(segment.segment_id, term.source, problem))
    return misses


def mark_for_retranslation(state: StateDocument, segment_ids: set[str]) -> StateDocument:
    """The state with these units pending, so the next translate run redoes them."""
    for segment_id in segment_ids:
        record = state.segments.get(segment_id)
        if record is not None:
            state.segments[segment_id] = record.model_copy(
                update={"status": SegmentStatus.PENDING, "error_message": None}
            )
    return state


def untranslated_with_terms(
    segments: Iterable[Segment], state: StateDocument, glossary: Glossary
) -> int:
    """Units holding a glossary term that are not translated yet; after
    --retranslate these are the outstanding work, which a list of misses in
    finished units would not show."""
    return sum(
        1
        for segment in segments
        if (record := state.segments.get(segment.segment_id)) is not None
        and record.status in (SegmentStatus.PENDING, SegmentStatus.ERROR)
        and glossary.terms_in(segment.source_content)
    )
