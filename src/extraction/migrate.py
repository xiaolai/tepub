"""Carry a workspace's translations and audio over to new segment ids.

Segments used to be keyed by file stem plus a hash of a positional xpath. The
new segmentation produces different units (a paragraph inside a blockquote is
no longer extracted twice) and new ids, so a workspace extracted before it
would otherwise lose every finished translation and synthesised audio file.

A legacy segment maps to a new unit only when both have the same file and the
same normalised source text; among equal texts, document order breaks the tie.
Matching by order alone assigns translations to the wrong paragraphs as soon as
the unit set changes, so nothing else is matched. Unmatched work is translated
or synthesised again, and the report says how much.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from lxml import html as lxml_html

from state.base import atomic_write
from state.models import ExtractMode, Segment, SegmentStatus
from state.store import backup_state, load_state, save_state

# 3: bare <a/> elements are no longer part of a unit's source.
SEGMENTS_FORMAT = 3


@dataclass
class ImportReport:
    mapped: int = 0
    finished_not_carried: int = 0
    audio_files_remapped: int = 0
    backups: list[Path] = field(default_factory=list)


def _match_key(segment: Segment) -> tuple[str, str]:
    text = segment.source_content
    if segment.extract_mode == ExtractMode.HTML:
        text = lxml_html.fragment_fromstring(text, create_parent="div").text_content()
    # Whitespace is ignored: old and new serialisations of the same HTML unit
    # differ only in the spacing between tags.
    return segment.file_path.as_posix(), "".join(text.split())


def legacy_mapping(old: list[Segment], new: list[Segment]) -> dict[str, str]:
    """old segment id -> new segment id, by file and source text, ties by order."""
    by_key: dict[tuple[str, str], deque[Segment]] = defaultdict(deque)
    for segment in sorted(old, key=lambda s: (s.file_path.as_posix(), s.metadata.order_in_file)):
        by_key[_match_key(segment)].append(segment)
    mapping: dict[str, str] = {}
    for segment in sorted(new, key=lambda s: (s.file_path.as_posix(), s.metadata.order_in_file)):
        candidates = by_key.get(_match_key(segment))
        if candidates:
            mapping[candidates.popleft().segment_id] = segment.segment_id
    return mapping


def import_legacy_workspace(
    work_dir: Path, state_file: Path, old: list[Segment], new: list[Segment]
) -> ImportReport:
    mapping = legacy_mapping(old, new)
    report = ImportReport(mapped=len(mapping))

    if state_file.exists():
        report.backups.append(backup_state(state_file))
        state = load_state(state_file)
        report.finished_not_carried = sum(
            1
            for old_id, record in state.segments.items()
            if record.status == SegmentStatus.COMPLETED and old_id not in mapping
        )
        state.segments = {
            mapping[old_id]: record.model_copy(update={"segment_id": mapping[old_id]})
            for old_id, record in state.segments.items()
            if old_id in mapping
        }
        save_state(state, state_file)

    for audio_state in sorted(work_dir.glob("audiobook*/audio_state.json")):
        report.backups.append(backup_state(audio_state))
        data = json.loads(audio_state.read_text(encoding="utf-8"))
        remapped = {}
        for old_id, record in data.get("segments", {}).items():
            if old_id in mapping:
                record["segment_id"] = mapping[old_id]
                remapped[mapping[old_id]] = record
        report.audio_files_remapped += len(remapped)
        data["segments"] = remapped
        atomic_write(audio_state, data)

    return report
