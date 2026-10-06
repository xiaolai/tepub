from __future__ import annotations

import hashlib
import os
import random
import tempfile
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp4 import MP4, MP4Cover
from rich.markup import escape
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from config import AppSettings
from console_singleton import get_console, live_display_enabled
from epub_io.reader import EpubReader
from epub_io.toc_utils import parse_toc_to_dict
from state.models import Segment
from state.store import load_segments

from .chapter_plan import (
    _build_spine_to_toc_map,
    _chapter_title,
    _document_titles,
    _load_custom_chapter_titles,
    _slugify,
    chapter_is_current,
    group_segments_into_chapters,
)
from .concat import ConcatError, concat_audio, container_duration, write_silence
from .cover import _prepare_cover
from .models import AudioSegmentStatus, AudioSessionConfig
from .state import load_state
from .statements import _render_statement
from .tagging import _book_authors, _book_title, _tag_audiobook

console = get_console()

# A filename component is limited to 255 bytes; a TOC entry can be a whole
# paragraph.
_MAX_SLUG_LENGTH = 150


def _file_slug(value: str) -> str:
    slug = _slugify(value)
    if len(slug) <= _MAX_SLUG_LENGTH:
        return slug
    # A digest of the whole slug keeps two long titles that share their first
    # 150 characters apart; the final file has no index prefix to do it.
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return f"{slug[: _MAX_SLUG_LENGTH - len(digest) - 1]}-{digest}"


def _partial_path(path: Path) -> Path:
    # Keeps the .m4a suffix so ffmpeg still picks the container from the name.
    return path.with_name(f"{path.stem}.partial{path.suffix}")


def _build_key(segment_pause_range: tuple[float, float], trailing_gap: bool) -> str:
    """What else, besides its segments, shaped a chapter's audio.

    The segment manifest alone let a chapter built with other pause timings, or
    built as the last chapter without the gap that now follows it, be reused.
    """
    low, high = segment_pause_range
    return f"segment_pause_range={low!r},{high!r}\ntrailing_gap={trailing_gap}"


def _manifest_paths(chapter_path: Path) -> tuple[Path, Path]:
    return (
        chapter_path.with_suffix(chapter_path.suffix + ".segments"),
        chapter_path.with_suffix(chapter_path.suffix + ".build"),
    )


def _apply_chapter_cover(
    chapter_path: Path, cover_data: bytes | None, cover_format: int
) -> None:
    """Give a chapter file the current artwork, or none, cached chapters included."""
    try:
        audio = MP4(chapter_path)
        if audio.tags is None:
            audio.add_tags()
        current = audio.tags.get("covr")
        if cover_data:
            wanted = [MP4Cover(cover_data, imageformat=cover_format)]
            if current == wanted and current[0].imageformat == cover_format:
                return
            audio.tags["covr"] = wanted
        elif current is not None:
            del audio.tags["covr"]
        else:
            return
        audio.save()
    except (MutagenError, OSError) as exc:
        # The artwork of an intermediate chapter file does not change the book,
        # so this warns rather than stops; it used to be discarded silently.
        console.print(
            f"[yellow]Could not set cover art on {escape(str(chapter_path))}: "
            f"{escape(str(exc))}[/yellow]"
        )


def _build_chapter(
    chapter_path: Path,
    file_path: str,
    segment_audio: list[Path],
    segment_pause_range: tuple[float, float],
    chapter_number: int,
    trailing_gap: bool,
) -> None:
    """Concatenate one chapter's segments, with pauses, into ``chapter_path``."""
    # Stable across processes; hash() on a path is salted per process.
    rng = random.Random(
        int.from_bytes(hashlib.sha256(str(file_path).encode()).digest()[:8], "big")
    )
    partial = _partial_path(chapter_path)
    # The scratch directory is named by tempfile, not by the chapter title: a
    # title such as "x/../../segments" pointed the cleanup at segment audio.
    with tempfile.TemporaryDirectory(prefix="chapter-", dir=chapter_path.parent) as tmp:
        scratch = Path(tmp)
        # Segments with pauses between them, then the gap before the next
        # chapter. Silence is written in the speech's own format; see
        # audiobook/concat.py for why that matters.
        like = segment_audio[0]
        parts: list[Path] = []
        for idx, segment_path in enumerate(segment_audio):
            parts.append(segment_path)
            if idx < len(segment_audio) - 1:
                pause_seconds = rng.uniform(*segment_pause_range)
                parts.append(
                    write_silence(scratch / f"pause_{idx}.m4a", pause_seconds, like=like)
                )
        if trailing_gap:
            chapter_gap_rng = random.Random(0xA10D10 + chapter_number)
            parts.append(
                write_silence(
                    scratch / "chapter_gap.m4a", chapter_gap_rng.uniform(2.0, 4.0), like=like
                )
            )
        try:
            # Built beside the chapter and moved over it only once verified, so a
            # failed rebuild cannot leave a truncated file under a valid manifest.
            concat_audio(parts, partial)
            os.replace(partial, chapter_path)
        finally:
            partial.unlink(missing_ok=True)


def _segment_audio(
    segments: list[Segment], audio_state, included_segment_ids: set[str] | None
) -> dict[str, Path]:
    """Audio file of each selected segment whose completed audio is on disk."""
    found: dict[str, Path] = {}
    for segment in segments:
        if included_segment_ids is not None and segment.segment_id not in included_segment_ids:
            continue
        seg_state = audio_state.segments.get(segment.segment_id)
        if not seg_state:
            continue
        if seg_state.status != AudioSegmentStatus.COMPLETED:
            continue
        if not seg_state.audio_path:
            continue
        audio_path = Path(seg_state.audio_path)
        if not audio_path.exists():
            continue
        found[segment.segment_id] = audio_path
    return found


def _cover_art(cover_path: Path | None) -> tuple[bytes | None, int]:
    """The prepared cover's bytes and MP4 image format, or None and JPEG."""
    if cover_path and cover_path.exists():
        # Detect format from file extension
        if cover_path.suffix.lower() == ".png":
            return cover_path.read_bytes(), MP4Cover.FORMAT_PNG
        return cover_path.read_bytes(), MP4Cover.FORMAT_JPEG
    return None, MP4Cover.FORMAT_JPEG


def _cached_duration(
    chapter_path: Path, ordered: list[Segment], audio_state, build_key: str
) -> float | None:
    """Duration of the cached chapter file, or None if it cannot be reused.

    Each chapter is checked on its own: one stale chapter used to force every
    other cached chapter to be recombined as well.
    """
    if not chapter_is_current(chapter_path, ordered, audio_state):
        return None
    _, build_manifest = _manifest_paths(chapter_path)
    try:
        if build_manifest.read_text(encoding="utf-8") != build_key:
            return None
        return container_duration(chapter_path)
    except (OSError, ConcatError):
        return None


def _rebuild_chapter(
    chapter_path: Path,
    file_path: str,
    title: str,
    segment_audio: list[tuple[str, Path]],
    segment_pause_range: tuple[float, float],
    chapter_number: int,
    trailing_gap: bool,
) -> float:
    """Build a chapter afresh and record what it was built from; its duration."""
    for segment_id, audio_path in segment_audio:
        if not audio_path.exists():
            raise FileNotFoundError(
                f"Audio for segment {segment_id} disappeared during assembly: {audio_path}"
            )
    segments_manifest, build_manifest = _manifest_paths(chapter_path)
    # Invalidate first: a rebuild that fails part-way must not leave the old
    # manifest vouching for whatever is at chapter_path.
    segments_manifest.unlink(missing_ok=True)
    build_manifest.unlink(missing_ok=True)
    _build_chapter(
        chapter_path,
        file_path,
        [audio_path for _, audio_path in segment_audio],
        segment_pause_range,
        chapter_number,
        trailing_gap=trailing_gap,
    )
    try:
        duration = container_duration(chapter_path)
    except ConcatError as exc:
        # A 0.0 duration is not a harmless default: chapter start times are
        # cumulative, so every later marker would be shifted or overlap. Fail
        # loudly instead of writing known-wrong markers.
        raise RuntimeError(
            f"Could not determine duration for chapter {title!r} "
            f"({chapter_path}). Chapter markers would be wrong for this "
            f"and every following chapter."
        ) from exc
    # Record which segments and settings this chapter was built from, so a
    # later run with different ones cannot reuse it.
    segments_manifest.write_text(
        "\n".join(segment_id for segment_id, _ in segment_audio), encoding="utf-8"
    )
    build_manifest.write_text(
        _build_key(segment_pause_range, trailing_gap), encoding="utf-8"
    )
    return duration


def assemble_audiobook(
    settings: AppSettings,
    input_epub: Path,
    session: AudioSessionConfig,
    state_path: Path,
    output_root: Path,
    included_segment_ids: set[str] | None = None,
) -> Path | None:
    """Assemble rendered segment audio into the final audiobook.

    ``included_segment_ids`` restricts assembly to the segments the caller
    actually selected. Without it, assembly re-scanned every stored segment and
    happily included audio from a previous run that the current inclusion/skip
    filters exclude.
    """
    audio_state = load_state(state_path)
    segments_doc = load_segments(settings.segments_file)

    reader = EpubReader(input_epub, settings)
    toc_map = parse_toc_to_dict(reader)
    doc_titles = _document_titles(reader)

    custom_chapters_map = _load_custom_chapter_titles(settings)

    # Build spine-to-TOC mapping for chapter grouping
    spine_to_toc = _build_spine_to_toc_map(reader, toc_map)

    # Group segments by TOC chapter (or by file if no TOC)
    segment_audio = _segment_audio(segments_doc.segments, audio_state, included_segment_ids)
    renderable_segments = [
        segment for segment in segments_doc.segments if segment.segment_id in segment_audio
    ]
    sorted_chapters = group_segments_into_chapters(renderable_segments, spine_to_toc)
    if not sorted_chapters:
        return None

    book_title = _book_title(reader)
    authors = _book_authors(reader)
    author_str = ", ".join(authors) if authors else "Unknown"

    # Statements are synthesised only once there is a book to put them in.
    opening_audio_path = _render_statement(
        "opening", settings.audiobook_opening_statement, session,
        output_root, book_title, author_str,
    )
    closing_audio_path = _render_statement(
        "closing", settings.audiobook_closing_statement, session,
        output_root, book_title, author_str,
    )

    chapters_dir = output_root / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    chapter_audios: list[tuple[str, Path, float]] = []
    segment_pause_range = session.segment_pause_range

    # Prepare cover image (needed for both chapter files and final audiobook)
    cover_path = _prepare_cover(output_root, reader, explicit_cover=session.cover_path)
    cover_data, cover_format = _cover_art(cover_path)

    console.print(f"[cyan]Combining {len(sorted_chapters)} chapters into final audiobook…[/cyan]")
    progress = Progress(
        TextColumn("Combining"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        disable=not live_display_enabled(console),
    )

    reused = 0
    with progress:
        combine_task = progress.add_task("chapter-mix", total=len(sorted_chapters))
        for chapter_number, (file_path, segments) in enumerate(sorted_chapters, start=1):
            title = _chapter_title(file_path, toc_map, doc_titles, custom_chapters_map)
            chapter_path = chapters_dir / f"{chapter_number:03d}-{_file_slug(title)}.m4a"
            # A TOC chapter can span several spine documents; order_in_file
            # alone interleaved their paragraphs.
            ordered = sorted(
                segments,
                key=lambda s: (s.metadata.spine_index, s.metadata.order_in_file),
            )
            trailing_gap = chapter_number < len(sorted_chapters)
            duration = _cached_duration(
                chapter_path, ordered, audio_state,
                _build_key(segment_pause_range, trailing_gap),
            )
            if duration is None:
                duration = _rebuild_chapter(
                    chapter_path,
                    file_path,
                    title,
                    [(seg.segment_id, segment_audio[seg.segment_id]) for seg in ordered],
                    segment_pause_range,
                    chapter_number,
                    trailing_gap,
                )
            else:
                reused += 1

            _apply_chapter_cover(chapter_path, cover_data, cover_format)
            chapter_audios.append((title, chapter_path, duration))
            progress.advance(combine_task)

    if reused:
        console.print(
            f"[cyan]Reused {reused} of {len(sorted_chapters)} cached chapter files.[/cyan]"
        )

    return _write_final(
        output_root,
        session,
        book_title,
        authors,
        cover_path,
        chapter_audios,
        opening_audio_path,
        closing_audio_path,
    )


def _write_final(
    audiobook_dir: Path,
    session: AudioSessionConfig,
    book_title: str,
    authors: list[str],
    cover_path: Path | None,
    chapter_audios: list[tuple[str, Path, float]],
    opening_audio_path: Path | None,
    closing_audio_path: Path | None,
) -> Path:
    """Join statements and chapters into the tagged final file."""
    # Use ffmpeg concat to avoid 4GB WAV limit and memory issues
    audiobook_dir.mkdir(parents=True, exist_ok=True)
    provider_suffix = "edgetts" if session.tts_provider == "edge" else "openaitts"
    final_name = f"{_file_slug(book_title)}@{provider_suffix}.m4a"
    workspace_path = audiobook_dir / final_name

    chapter_markers: list[tuple[int, str]] = []
    current_position_seconds = 0.0  # Use float for precision
    parts: list[Path] = []
    like = chapter_audios[0][1]

    if opening_audio_path and opening_audio_path.exists():
        opening_silence_path = write_silence(
            audiobook_dir / "opening_silence.m4a",
            random.Random(0xDEADBEEF).uniform(2.0, 4.0),
            like=like,
        )
        parts += [opening_audio_path, opening_silence_path]
        # Durations come from the files themselves, so markers match the audio.
        current_position_seconds += container_duration(opening_audio_path)
        current_position_seconds += container_duration(opening_silence_path)

    for idx, (title, chapter_path, duration_seconds) in enumerate(chapter_audios):
        safe_title = title.strip() if isinstance(title, str) else ""
        if not safe_title:
            safe_title = f"Chapter {idx + 1}"
        chapter_markers.append((int(current_position_seconds * 1000), safe_title))
        parts.append(chapter_path)
        # The chapter's own duration already includes the gap at its end.
        current_position_seconds += duration_seconds

    if closing_audio_path and closing_audio_path.exists():
        closing_silence_path = write_silence(
            audiobook_dir / "closing_silence.m4a",
            random.Random(0xC105ED).uniform(2.0, 4.0),
            like=like,
        )
        parts += [closing_silence_path, closing_audio_path]

    console.print("[cyan]Creating final M4A audiobook…[/cyan]")
    # The previous book is replaced only by a joined, verified and tagged one; a
    # failed rebuild used to destroy it first.
    partial = _partial_path(workspace_path)
    try:
        concat_audio(parts, partial)
        _tag_audiobook(partial, book_title, authors, cover_path, chapter_markers)
        os.replace(partial, workspace_path)
    finally:
        partial.unlink(missing_ok=True)
        # The silences are cheap to make again; the statements stay cached.
        for name in ("opening_silence.m4a", "closing_silence.m4a"):
            (audiobook_dir / name).unlink(missing_ok=True)

    # Keep audiobook in work_dir structure instead of moving to EPUB parent
    return workspace_path
