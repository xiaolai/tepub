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

A translation is carried in the form its new unit takes. When a unit changed
between text and HTML, its translation is converted, and one that became HTML
must then meet the markup contract a fresh translation meets, or it is
translated again: a plain translation cannot carry a link the unit now keeps.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import html

from lxml import html as lxml_html

from extraction.segments import clean_markup
from state.base import atomic_write
from state.models import ExtractMode, Segment, SegmentStatus
from state.store import backup_state, load_state, save_state
from translation.markup import markup_mismatch

# 3: bare <a/> elements are no longer part of a unit's source.
SEGMENTS_FORMAT = 3


@dataclass
class ImportReport:
    mapped: int = 0
    finished_not_carried: int = 0
    audio_files_remapped: int = 0
    backups: list[Path] = field(default_factory=list)


def _plain_text(markup: str) -> str:
    return lxml_html.fragment_fromstring(markup, create_parent="div").text_content()


def _carried_translation(translation: str, old: Segment, new: Segment) -> str | None:
    """The translation in its new unit's form, or None if it must be redone."""
    if new.extract_mode == ExtractMode.TEXT:
        return _plain_text(translation) if old.extract_mode == ExtractMode.HTML else translation
    if old.extract_mode == ExtractMode.TEXT:
        converted = html.escape(translation, quote=False)
        return None if markup_mismatch(new.source_content, converted) else converted
    # Both HTML: drop what the new rule no longer sends, such as bare <a/>.
    return clean_markup(translation)


def _match_key(segment: Segment) -> tuple[str, str]:
    text = segment.source_content
    if segment.extract_mode == ExtractMode.HTML:
        text = _plain_text(text)
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
    old_by_id = {segment.segment_id: segment for segment in old}
    new_by_id = {segment.segment_id: segment for segment in new}

    if state_file.exists():
        report.backups.append(backup_state(state_file))
        state = load_state(state_file)
        carried = {}
        for old_id, record in state.segments.items():
            new_id = mapping.get(old_id)
            update: dict = {"segment_id": new_id}
            if new_id is not None and record.translation is not None:
                update["translation"] = _carried_translation(
                    record.translation, old_by_id[old_id], new_by_id[new_id]
                )
            if new_id is None or ("translation" in update and update["translation"] is None):
                if record.status == SegmentStatus.COMPLETED:
                    report.finished_not_carried += 1
                continue
            carried[new_id] = record.model_copy(update=update)
        state.segments = carried
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
