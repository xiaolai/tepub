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

import html
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

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
# 6: loose container text and child tails become injectable units, so unit
#    positions shift; old workspaces must be migrated rather than reused.
# 7: xml:id travels in the extracted source and anchor attributes, so a
#    translated copy can keep its anchor associations.
SEGMENTS_FORMAT = 7


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
    # Then check it against the *new* source: a translation carried across a
    # changed link used to be kept because matching ignored markup.
    cleaned = clean_markup(translation)
    return None if markup_mismatch(new.source_content, cleaned) else cleaned


_ITEM_TAGS = ("li", "td", "th", "dt", "dd")


def _items(markup: str) -> list[str]:
    """Inner markup of each list item, cell or entry, outermost only, in order."""
    root = lxml_html.fragment_fromstring(markup, create_parent="div")
    found = []
    for element in root.iter(*_ITEM_TAGS):
        if any(ancestor.tag in _ITEM_TAGS for ancestor in element.iterancestors()):
            continue
        # The leading text is escaped like the serialized children: copied raw,
        # a literal "&lt;b&gt;" became a <b> element when the fragment was parsed.
        inner = html.escape(element.text or "", quote=False) + "".join(
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
    return (html.escape(element.text or "", quote=False) + "".join(
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
        if (
            segment.segment_id in mapping
            or record is None
            or segment.extract_mode != ExtractMode.HTML
        ):
            continue
        sources, translated = _items(segment.source_content), _items(record.translation or "")
        if not sources or len(sources) != len(translated):
            continue
        for source, item_translation in zip(sources, translated, strict=True):
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


#: List and table structure. Whitespace between these tags is serialisation:
#: "<li>One</li>\n<li>Two</li>" is the same markup as the one-line spelling,
#: and its items are joined by structure, not by a word space. Elsewhere a
#: block boundary is a word boundary: deleting the space in
#: "<p>Now</p> <p>here.</p>" made it match "Nowhere." and carried a
#: translation onto a different paragraph.
_STRUCTURAL_TAG = "dd|dl|dt|li|ol|table|tbody|td|tfoot|th|thead|tr|ul"
_SERIALISATION_SPACE = re.compile(
    rf"(</?(?:{_STRUCTURAL_TAG})\b[^>]*>)\s+(?=</?(?:{_STRUCTURAL_TAG})\b)"
)


def _strip_serialisation_space(markup: str) -> str:
    return _SERIALISATION_SPACE.sub(r"\1", markup)


def _match_key(segment: Segment) -> tuple[str, str]:
    text = segment.source_content
    if segment.extract_mode == ExtractMode.HTML:
        # Whitespace inside text separates words and must not be deleted:
        # removing all of it made "Now here." match "Nowhere." and carried a
        # translation onto the wrong paragraph.
        text = _plain_text(_strip_serialisation_space(text))
    return segment.file_path.as_posix(), " ".join(text.split())


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


# Written once the migrated files are prepared and before any is replaced;
# removed once the new segments are saved (finish_legacy_import).
_JOURNAL = "migration.json"


def _read_journal(work_dir: Path) -> dict | None:
    journal = work_dir / _JOURNAL
    if not journal.exists():
        return None
    return json.loads(journal.read_text(encoding="utf-8"))


def _journal_sources(
    journal: dict | None, work_dir: Path, state_file: Path
) -> dict[Path, Path]:
    """live file -> the copy an interrupted import took of it before
    rewriting it; empty when no import was interrupted."""
    if journal is None:
        return {}
    sources = {}
    for name, backup in journal["backups"].items():
        live = state_file if name == "state" else work_dir / name
        sources[live] = live.with_name(backup)
    return sources


def finish_legacy_import(work_dir: Path) -> None:
    """Called once the new segments document is saved: the import is final."""
    (work_dir / _JOURNAL).unlink(missing_ok=True)


def _audio_rename_plan(
    audio_state: Path, data: dict, mapping: dict[str, str]
) -> list[dict]:
    """Rename each carried clip to its new segment id, without overwriting one.

    Segment ids name positions, so a new id can already have a clip from a
    different old unit. Synthesis names its output after the id, so leaving the
    carried clip under the old name let a later synthesis overwrite it.

    The whole plan is settled before any file moves, and is journalled, so a
    retry replays it instead of reading the already-renamed files as if they
    were the old ones: with a swap (A->B, B->A) that swapped the audio again
    and gave each unit the other's clip.
    """
    segments_dir = audio_state.parent / "segments"
    moves: list[tuple[str, str, Path]] = []
    for old_id, record in (data.get("segments") or {}).items():
        new_id = mapping.get(old_id)
        if new_id is None:
            continue
        candidates: list[Path] = []
        stored = record.get("audio_path")
        if stored:
            path = Path(stored)
            candidates.append(path if path.is_absolute() else audio_state.parent / path)
        if segments_dir.is_dir():
            candidates.extend(sorted(segments_dir.glob(f"{old_id}.*")))
        source = next((candidate for candidate in candidates if candidate.is_file()), None)
        if source is not None:
            moves.append((old_id, new_id, source))
    if not moves:
        return []

    vacated = {source.resolve() for _old_id, _new_id, source in moves}
    reserved: set[Path] = set()
    plan: list[dict] = []
    for old_id, new_id, source in moves:
        target = source.with_name(f"{new_id}{source.suffix}")
        counter = 1
        while target in reserved or (target.exists() and target.resolve() not in vacated):
            target = source.with_name(f"{new_id}-{counter}{source.suffix}")
            counter += 1
        reserved.add(target)
        stat = source.stat()
        plan.append(
            {
                "old": old_id,
                "new": new_id,
                "source": str(source),
                # Deterministic so an interrupted staging can be resumed.
                "temporary": str(source.with_name(f".migrate-{old_id}{source.suffix}")),
                "target": str(target),
                # A rename keeps the inode, so this says which file the clip is
                # wherever it now sits. Names alone cannot: after a swap
                # (A->B, B->A) both old names exist again, holding each other's
                # audio, and a replay would swap them back.
                "identity": [stat.st_dev, stat.st_ino],
            }
        )
    return plan


def _is_the_clip(path: Path, identity: list[int]) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return [stat.st_dev, stat.st_ino] == identity


def _apply_audio_renames(plan: list[dict]) -> None:
    """Carry out a plan, or resume one interrupted part way; idempotent.

    Every source is staged aside before any takes its final name, so swapping
    A->B and B->A cannot destroy one of them. Each step checks the filesystem
    first, and identifies a clip by its inode rather than its name, so running
    the plan again after a crash changes nothing.
    """
    for entry in plan:
        source, temporary = Path(entry["source"]), Path(entry["temporary"])
        if temporary.exists() or _is_the_clip(Path(entry["target"]), entry["identity"]):
            continue
        if _is_the_clip(source, entry["identity"]):
            source.replace(temporary)
    for entry in plan:
        target, temporary = Path(entry["target"]), Path(entry["temporary"])
        if _is_the_clip(target, entry["identity"]):
            continue
        if temporary.exists() and not target.exists():
            temporary.replace(target)


def _apply_audio_paths(data: dict, plan: list[dict]) -> None:
    """Point every renamed clip's record at the name the plan gives it."""
    targets = {entry["old"]: entry["target"] for entry in plan}
    for old_id, record in (data.get("segments") or {}).items():
        target = targets.get(old_id)
        if target is not None:
            record["audio_path"] = target


def import_legacy_workspace(
    work_dir: Path, state_file: Path, old: list[Segment], new: list[Segment]
) -> ImportReport:
    """Rewrite the state and audio states under the new segment ids.

    Every migrated document is built before any is written, so a file that
    fails to read or convert changes nothing. The caller saves the new
    segments document after this returns, then calls finish_legacy_import; if
    it never gets there, the next extraction imports again. The files are by
    then keyed by new ids, and those are positional, like the old ones: the
    same id can name another paragraph in each, so they cannot be told apart
    and imported a second time. A journal names the copies taken before the
    first rewrite, and a retry imports from those.
    """
    mapping = legacy_mapping(old, new)
    report = ImportReport(mapped=len(mapping))
    old_by_id = {segment.segment_id: segment for segment in old}
    new_by_id = {segment.segment_id: segment for segment in new}
    journal = _read_journal(work_dir)
    sources = _journal_sources(journal, work_dir, state_file)
    renames: dict[str, list[dict]] = (journal or {}).get("audio_renames", {})

    state = None
    if state_file.exists():
        state = load_state(sources.get(state_file, state_file))
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

    audio_states: list[tuple[Path, dict, list[dict]]] = []
    for audio_state in sorted(work_dir.glob("audiobook*/audio_state.json")):
        source = sources.get(audio_state, audio_state)
        data = json.loads(source.read_text(encoding="utf-8"))
        key = audio_state.relative_to(work_dir).as_posix()
        # A retry must replay the first attempt's plan: the clips on disk are
        # already under their new names, so planning again would rename them a
        # second time and, for a swap, hand each unit the other's audio.
        plan = renames.get(key)
        if plan is None:
            plan = _audio_rename_plan(audio_state, data, mapping)
        _apply_audio_paths(data, plan)
        remapped = {}
        for old_id, record in data.get("segments", {}).items():
            if old_id in mapping:
                record["segment_id"] = mapping[old_id]
                remapped[mapping[old_id]] = record
        report.audio_files_remapped += len(remapped)
        data["segments"] = remapped
        audio_states.append((audio_state, data, plan))

    targets = ([state_file] if state is not None else []) + [
        path for path, _data, _plan in audio_states
    ]
    if journal is None:
        for target in targets:
            sources[target] = backup_state(target)
        backups = {
            "state" if target == state_file else target.relative_to(work_dir).as_posix(): (
                sources[target].name
            )
            for target in targets
        }
        # Written before any clip moves: the plan names where each one is
        # going, so a crash during the (resumable) moves leaves enough behind.
        atomic_write(
            work_dir / _JOURNAL,
            {
                "backups": backups,
                "audio_renames": {
                    path.relative_to(work_dir).as_posix(): plan
                    for path, _data, plan in audio_states
                    if plan
                },
            },
        )
    report.backups = [sources[target] for target in targets if target in sources]

    for _audio_state, _data, plan in audio_states:
        _apply_audio_renames(plan)
    if state is not None:
        save_state(state, state_file)
    for audio_state, data, _plan in audio_states:
        atomic_write(audio_state, data)

    return report
