from __future__ import annotations

import io
import struct
from collections.abc import Iterable, Sequence
from pathlib import Path

from mutagen._util import insert_bytes, resize_bytes
from mutagen.mp4 import MP4, Atom, Atoms, MP4Chapters, MP4Tags

ChapterTuple = tuple[float, str]


def _build_chpl_payload(chapters: Sequence[ChapterTuple], timescale: int) -> bytes:
    if timescale <= 0:
        timescale = 1000

    body = bytearray()
    body.append(len(chapters))

    for seconds, title in chapters:
        safe_title = (title or "").strip()
        if not safe_title:
            safe_title = "Chapter"
        encoded = safe_title.encode("utf-8")[:255]
        start = int(round(seconds * timescale * 10000))
        body.extend(struct.pack(">Q", start))
        body.append(len(encoded))
        body.extend(encoded)

    header = struct.pack(">I", 0x01000000) + b"\x00\x00\x00\x00"
    return header + body


def _movie_timescale(fileobj: io.BufferedRandom, atoms: Atoms) -> int:
    try:
        mvhd_atom = atoms.path(b"moov", b"mvhd")[-1]
    except KeyError:
        return 1000

    chapters = MP4Chapters()
    chapters._parse_mvhd(mvhd_atom, fileobj)
    return chapters._timescale or 1000


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
        timescale = _movie_timescale(fh, atoms)
        payload = _build_chpl_payload(seconds_markers, timescale)
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

    # Optional load to ensure chapters read back; ignore failures silently
    try:
        mp4 = MP4(mp4_path)
        _ = mp4.chapters
    except Exception:
        pass
