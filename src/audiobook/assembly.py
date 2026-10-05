from __future__ import annotations

import hashlib
import logging
import random
import shutil
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from config import AppSettings
from console_singleton import get_console
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
from .concat import concat_audio, container_duration, write_silence
from .cover import _prepare_cover
from .models import AudioSegmentStatus, AudioSessionConfig
from .state import load_state
from .statements import _render_statement
from .tagging import _book_authors, _book_title, _tag_audiobook

logger = logging.getLogger(__name__)
console = get_console()



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
    book_title = _book_title(reader)
    authors = _book_authors(reader)
    author_str = ", ".join(authors) if authors else "Unknown"

    custom_chapters_map = _load_custom_chapter_titles(settings)

    # Generate opening and closing statement audio
    opening_audio_path = _render_statement(
        "opening", settings.audiobook_opening_statement, session,
        output_root, book_title, author_str,
    )
    closing_audio_path = _render_statement(
        "closing", settings.audiobook_closing_statement, session,
        output_root, book_title, author_str,
    )

    # Build spine-to-TOC mapping for chapter grouping
    spine_to_toc = _build_spine_to_toc_map(reader, toc_map)

    # Group segments by TOC chapter (or by file if no TOC)
    renderable_segments: list[Segment] = []
    for segment in segments_doc.segments:
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

        renderable_segments.append(segment)

    sorted_chapters = group_segments_into_chapters(renderable_segments, spine_to_toc)
    if not sorted_chapters:
        return None

    chapters_dir = output_root / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    chapter_audios: list[tuple[str, Path, float]] = []
    segment_pause_range = getattr(session, "segment_pause_range", (2.0, 4.0))

    # Prepare cover image (needed for both chapter files and final audiobook)
    explicit_cover = session.cover_path
    cover_path = _prepare_cover(output_root, reader, explicit_cover=explicit_cover)
    cover_data = None
    cover_format = MP4Cover.FORMAT_JPEG  # Default format
    if cover_path and cover_path.exists():
        cover_data = cover_path.read_bytes()
        # Detect format from file extension
        if cover_path.suffix.lower() == ".png":
            cover_format = MP4Cover.FORMAT_PNG

    # Check if chapter files already exist
    expected_chapters = []
    for index, (file_path, segments) in enumerate(sorted_chapters, start=1):
        title = _chapter_title(file_path, toc_map, doc_titles, custom_chapters_map)
        chapter_path = chapters_dir / f"{index:03d}-{_slugify(title)}.m4a"
        expected_chapters.append((title, chapter_path, file_path, segments))

    def _chapter_is_current(chapter_path: Path, segments: list[Segment]) -> bool:
        return chapter_is_current(chapter_path, segments, audio_state)

    all_chapters_exist = all(
        _chapter_is_current(path, segments) for _, path, _, segments in expected_chapters
    )

    if all_chapters_exist:
        console.print(f"[cyan]Found existing {len(expected_chapters)} chapter files, skipping recombination…[/cyan]")
        # Load existing chapter files and calculate durations
        for title, chapter_path, _, _ in expected_chapters:
            try:
                duration = container_duration(chapter_path)
                chapter_audios.append((title, chapter_path, duration))
            except Exception:
                # If any file is invalid, we'll need to regenerate all
                chapter_audios = []
                all_chapters_exist = False
                break

    if not all_chapters_exist:
        console.print(f"[cyan]Combining {len(sorted_chapters)} chapters into final audiobook…[/cyan]")
        progress = Progress(
            TextColumn("Combining"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        )

        with progress:
            combine_task = progress.add_task("chapter-mix", total=len(sorted_chapters))
            for chapter_number, (title, chapter_path, file_path, segments) in enumerate(
                expected_chapters, start=1
            ):
                # Stable across processes; hash() on a path is salted per process.
                rng = random.Random(
                    int.from_bytes(
                        hashlib.sha256(str(file_path).encode()).digest()[:8], "big"
                    )
                )

                # Get available segment files
                available_segment_paths = []
                for segment in sorted(segments, key=lambda s: s.metadata.order_in_file):
                    seg_state = audio_state.segments.get(segment.segment_id)
                    if not seg_state or not seg_state.audio_path:
                        continue
                    audio_path = Path(seg_state.audio_path)
                    if not audio_path.exists():
                        continue
                    available_segment_paths.append(audio_path)

                if not available_segment_paths:
                    progress.advance(combine_task)
                    continue

                # Create silence files for pauses between segments
                chapter_temp_dir = chapters_dir / f"temp_{title[:20]}"
                chapter_temp_dir.mkdir(parents=True, exist_ok=True)

                # Segments with pauses between them, then the gap before the next
                # chapter. Silence is written in the speech's own format; see
                # audiobook/concat.py for why that matters.
                like = available_segment_paths[0]
                parts: list[Path] = []
                for idx, segment_path in enumerate(available_segment_paths):
                    parts.append(segment_path)
                    if idx < len(available_segment_paths) - 1:
                        pause_seconds = rng.uniform(*segment_pause_range)
                        parts.append(
                            write_silence(
                                chapter_temp_dir / f"pause_{idx}.m4a", pause_seconds, like=like
                            )
                        )

                if chapter_number < len(sorted_chapters):
                    chapter_gap_rng = random.Random(0xA10D10 + chapter_number)
                    chapter_gap_seconds = chapter_gap_rng.uniform(2.0, 4.0)
                    parts.append(
                        write_silence(
                            chapter_temp_dir / "chapter_gap.m4a", chapter_gap_seconds, like=like
                        )
                    )

                concat_audio(parts, chapter_path)

                # Clean up temp directory
                shutil.rmtree(chapter_temp_dir, ignore_errors=True)
                # Record which segments this chapter was built from, so a later
                # run with a different segment set cannot reuse it.
                chapter_path.with_suffix(chapter_path.suffix + ".segments").write_text(
                    "\n".join(seg.segment_id for seg in segments), encoding="utf-8"
                )

                # Add cover art to chapter M4A file
                if cover_data:
                    try:
                        audio = MP4(chapter_path)
                        audio["covr"] = [MP4Cover(cover_data, imageformat=cover_format)]
                        audio.save()
                    except Exception:
                        pass  # Silently ignore cover art failures

                # Get actual duration from the created file (using ffprobe for accuracy)
                try:
                    actual_duration = container_duration(chapter_path)
                except Exception as exc:
                    # A 0.0 duration is not a harmless default: chapter start times
                    # are cumulative, so every later marker would be shifted or
                    # overlap. Fail loudly instead of writing known-wrong markers.
                    raise RuntimeError(
                        f"Could not determine duration for chapter {title!r} "
                        f"({chapter_path}). Chapter markers would be wrong for this "
                        f"and every following chapter."
                    ) from exc

                chapter_audios.append((title, chapter_path, actual_duration))
                progress.advance(combine_task)

    if not chapter_audios:
        return None

    # Use ffmpeg concat to avoid 4GB WAV limit and memory issues
    audiobook_dir = output_root
    audiobook_dir.mkdir(parents=True, exist_ok=True)
    provider_suffix = "edgetts" if session.tts_provider == "edge" else "openaitts"
    final_name = f"{_slugify(book_title)}@{provider_suffix}.m4a"
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
    concat_audio(parts, workspace_path)

    # The silences are cheap to make again; the statements stay cached.
    for name in ("opening_silence.m4a", "closing_silence.m4a"):
        (audiobook_dir / name).unlink(missing_ok=True)

    _tag_audiobook(workspace_path, book_title, authors, cover_path, chapter_markers)

    # Keep audiobook in work_dir structure instead of moving to EPUB parent
    return workspace_path
