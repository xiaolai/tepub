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
from state.models import ExtractMode, Segment, SegmentStatus, TranslationRecord
from state.store import backup_state, load_state, save_state
from translation.markup import markup_mismatch

# 3: bare <a/> elements are no longer part of a unit's source.
# 4: lists, tables and definition lists over SPLIT_ABOVE_CHARS are split into
#    their items, cells and entries.
# 5: that size is measured on the markup sent, not the text; containers with
#    text of their own over it are split too; and a unit's leading text is
#    escaped like the rest of its source.
SEGMENTS_FORMAT = 5


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


_ITEM_TAGS = ("li", "td", "th", "dt", "dd")


def _items(markup: str) -> list[str]:
    """Inner markup of each list item, cell or entry, outermost only, in order."""
    root = lxml_html.fragment_fromstring(markup, create_parent="div")
    found = []
    for element in root.iter(*_ITEM_TAGS):
        if any(ancestor.tag in _ITEM_TAGS for ancestor in element.iterancestors()):
            continue
        inner = (element.text or "") + "".join(
            lxml_html.tostring(child, encoding="unicode") for child in element
        )
        found.append(inner.strip())
    return found


_WRAPPERS = frozenset({"p", "div", "blockquote", "section", "span"})


def _only_child(markup: str):
    root = lxml_html.fragment_fromstring(markup, create_parent="div")
    children = [child for child in root if isinstance(child.tag, str)]
    if (root.text or "").strip() or len(children) != 1 or (children[0].tail or "").strip():
        return None
    return children[0]


def _inner(element) -> str:
    return ((element.text or "") + "".join(
        lxml_html.tostring(child, encoding="unicode") for child in element
    )).strip()


def _unwrap(source: str, translation: str) -> tuple[str, str]:
    """Step into a wrapper both sides share: an item holding only a paragraph,
    <li><p>…</p></li>, is now split into that paragraph, whose translation must
    not carry the <p> of the item's, or the book gets a <p> inside a <p>."""
    while True:
        wrapped, translated = _only_child(source), _only_child(translation)
        if (
            wrapped is None
            or translated is None
            or wrapped.tag != translated.tag
            or wrapped.tag not in _WRAPPERS
        ):
            return source, translation
        source, translation = _inner(wrapped), _inner(translated)


def _split_whole_lists(
    old: list[Segment],
    new: list[Segment],
    mapping: dict[str, str],
    records: dict[str, TranslationRecord],
) -> tuple[dict[str, TranslationRecord], set[str]]:
    """Records for new item units, from lists, tables and definition lists that
    were one unit and are now split into their items (format 4), and the ids of
    the old units carried this way.

    A whole list's translation kept its items, since the markup contract holds
    their count, so its nth item is the translation of the source's nth item.
    Each pair goes to the new unit with that item's text; a list whose counts
    differ is translated again.
    """
    taken = set(mapping.values())
    free: dict[tuple[str, str], deque[Segment]] = defaultdict(deque)
    for segment in sorted(new, key=lambda s: (s.file_path.as_posix(), s.metadata.order_in_file)):
        if segment.segment_id not in taken:
            free[_match_key(segment)].append(segment)
    carried: dict[str, TranslationRecord] = {}
    used: set[str] = set()
    for segment in old:
        record = records.get(segment.segment_id)
        if segment.segment_id in mapping or record is None or segment.extract_mode != ExtractMode.HTML:
            continue
        sources, translated = _items(segment.source_content), _items(record.translation or "")
        if not sources or len(sources) != len(translated):
            continue
        for source, item_translation in zip(sources, translated):
            source, item_translation = _unwrap(source, item_translation)
            item = Segment(
                segment_id=segment.segment_id,
                file_path=segment.file_path,
                xpath=segment.xpath,
                extract_mode=ExtractMode.HTML,
                source_content=source,
                metadata=segment.metadata,
            )
            candidates = free.get(_match_key(item))
            if not candidates:
                continue
            target = candidates.popleft()
            converted = _carried_translation(item_translation, item, target)
            if converted is not None:
                carried[target.segment_id] = record.model_copy(
                    update={"segment_id": target.segment_id, "translation": converted}
                )
                used.add(segment.segment_id)
    return carried, used


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
        completed = {
            old_id: record
            for old_id, record in state.segments.items()
            if record.status == SegmentStatus.COMPLETED and record.translation is not None
        }
        split, split_from = _split_whole_lists(old, new, mapping, completed)
        carried.update(split)
        report.mapped += len(split)
        report.finished_not_carried -= len(split_from)
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
