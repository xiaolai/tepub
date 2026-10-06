from __future__ import annotations

import io
import struct
from collections.abc import Iterable, Sequence
from pathlib import Path

from mutagen._util import insert_bytes, resize_bytes
from mutagen.mp4 import MP4, Atom, Atoms, MP4Tags

ChapterTuple = tuple[float, str]

# The Nero chpl atom stores start times in 100-nanosecond ticks. That unit is
# fixed by the format and does not depend on the movie's own timescale; mutagen
# and ffprobe both divide by it directly.
CHPL_TICKS_PER_SECOND = 10_000_000


def _build_chpl_payload(chapters: Sequence[ChapterTuple]) -> bytes:
    # The chpl format stores the chapter count in a single byte. Without this check
    # bytearray.append() raised a bare ValueError from deep inside the packer.
    if len(chapters) > 255:
        raise ValueError(
            f"chpl chapter atom supports at most 255 chapters, got {len(chapters)}. "
            "Reduce the chapter count or disable chpl chapter markers."
        )

    body = bytearray()
    body.append(len(chapters))

    for seconds, title in chapters:
        safe_title = (title or "").strip()
        if not safe_title:
            safe_title = "Chapter"
        # Titles are length-prefixed with one byte, so cap at 255 bytes. Slicing
        # encoded UTF-8 can cut a multibyte character in half and yield invalid
        # metadata, so drop any partial trailing sequence after truncating.
        encoded = safe_title.encode("utf-8")[:255]
        encoded = encoded.decode("utf-8", errors="ignore").encode("utf-8")
        start = int(round(seconds * CHPL_TICKS_PER_SECOND))
        body.extend(struct.pack(">Q", start))
        body.append(len(encoded))
        body.extend(encoded)

    header = struct.pack(">I", 0x01000000) + b"\x00\x00\x00\x00"
    return header + body


def _apply_delta(
    helper: MP4Tags,
    fileobj: io.BufferedRandom,
    parents: Iterable[Atom],
    atoms: Atoms,
    delta: int,
    offset: int,
) -> None:
    if delta == 0:
        return
    helper._MP4Tags__update_parents(fileobj, list(parents), delta)
    helper._MP4Tags__update_offsets(fileobj, atoms, delta, offset)


def _replace_existing_chpl(
    helper: MP4Tags,
    fileobj: io.BufferedRandom,
    atoms: Atoms,
    chpl_atom: bytes,
    path: list[Atom],
) -> None:
    target = path[-1]
    offset = target.offset
    original_length = target.length
    resize_bytes(fileobj, original_length, len(chpl_atom), offset)
    fileobj.seek(offset)
    fileobj.write(chpl_atom)
    delta = len(chpl_atom) - original_length
    _apply_delta(helper, fileobj, path[:-1], atoms, delta, offset)


def _append_to_udta(
    helper: MP4Tags,
    fileobj: io.BufferedRandom,
    atoms: Atoms,
    chpl_atom: bytes,
    udta_path: list[Atom],
) -> None:
    udta_atom = udta_path[-1]
    insert_offset = udta_atom.offset + udta_atom.length
    insert_bytes(fileobj, len(chpl_atom), insert_offset)
    fileobj.seek(insert_offset)
    fileobj.write(chpl_atom)
    _apply_delta(helper, fileobj, udta_path, atoms, len(chpl_atom), insert_offset)


def _create_udta_with_chpl(
    helper: MP4Tags,
    fileobj: io.BufferedRandom,
    atoms: Atoms,
    chpl_atom: bytes,
    moov_path: list[Atom],
) -> None:
    udta_atom = Atom.render(b"udta", chpl_atom)
    insert_offset = moov_path[-1].offset + moov_path[-1].length
    insert_bytes(fileobj, len(udta_atom), insert_offset)
    fileobj.seek(insert_offset)
    fileobj.write(udta_atom)
    _apply_delta(helper, fileobj, moov_path, atoms, len(udta_atom), insert_offset)


def write_chapter_markers(mp4_path: Path, markers: Sequence[tuple[int, str]]) -> None:
    if not markers:
        return

    seconds_markers: list[ChapterTuple] = [
        (start_ms / 1000.0, title) for start_ms, title in markers
    ]

    with open(mp4_path, "r+b") as fh:
        atoms = Atoms(fh)
        payload = _build_chpl_payload(seconds_markers)
        chpl_atom = Atom.render(b"chpl", payload)
        helper = MP4Tags()

        try:
            path = atoms.path(b"moov", b"udta", b"chpl")
        except KeyError:
            try:
                udta_path = atoms.path(b"moov", b"udta")
            except KeyError:
                moov_path = atoms.path(b"moov")
                _create_udta_with_chpl(helper, fh, atoms, chpl_atom, moov_path)
            else:
                _append_to_udta(helper, fh, atoms, chpl_atom, udta_path)
        else:
            _replace_existing_chpl(helper, fh, atoms, chpl_atom, path)

    # Read the chapters back and hold them to what was meant. Parsing alone is
    # not enough: chapters written in the wrong unit parse perfectly and point
    # at the wrong time, which is how every marker landed 24x late unnoticed.
    _verify_chapter_starts(mp4_path, seconds_markers)


class ChapterVerificationError(RuntimeError):
    """Chapter markers were written but do not read back as intended."""


def _verify_chapter_starts(mp4_path: Path, expected: Sequence[ChapterTuple]) -> None:
    try:
        found = list(MP4(mp4_path).chapters or [])
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise ChapterVerificationError(
            f"chapter markers in {mp4_path.name} could not be read back: {exc}"
        ) from exc
    if len(found) != len(expected):
        raise ChapterVerificationError(
            f"{mp4_path.name}: wrote {len(expected)} chapter markers, read back {len(found)}"
        )
    for index, (chapter, (seconds, _title)) in enumerate(zip(found, expected, strict=True)):
        if abs(chapter.start - seconds) > 0.01:
            raise ChapterVerificationError(
                f"{mp4_path.name}: chapter {index + 1} should start at {seconds:.3f} s "
                f"but reads back at {chapter.start:.3f} s"
            )
