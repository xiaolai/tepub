from __future__ import annotations

import hashlib
import logging
import random
import re
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from ebooklib import ITEM_IMAGE

try:
    from mutagen.mp4 import MP4, MP4Chapter, MP4Cover
except ImportError:
    from mutagen.mp4 import MP4, MP4Cover

    MP4Chapter = None
from PIL import Image
from pydub import AudioSegment
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.resources import get_item_by_href
from epub_io.toc_utils import parse_toc_to_dict
from state.models import Segment
from state.store import load_segments

from .cover import find_spine_cover_candidate
from .models import AudioSegmentStatus, AudioSessionConfig
from .mp4chapters import write_chapter_markers
from .state import load_state

logger = logging.getLogger(__name__)
console = get_console()


def _slugify(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_\-]", "", value)
    return value or "audiobook"


def _get_audio_duration(audio_path: Path) -> float:
    """Get accurate audio duration in seconds using ffprobe.

    Uses ffprobe instead of pydub's duration_seconds because pydub
    underreports duration for M4A/AAC files (VBR encoding issue).

    Args:
        audio_path: Path to audio file (M4A, MP3, etc.)

    Returns:
        Duration in seconds as float
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _extract_narrator_name(voice_id: str) -> str:
    """Extract friendly narrator name from voice ID.

    Examples:
        en-US-GuyNeural -> Guy
        en-US-JennyNeural -> Jenny
        alloy -> alloy
    """
    # Remove language prefix (e.g., "en-US-")
    parts = voice_id.split("-")
    if len(parts) >= 3:
        name_part = "-".join(parts[2:])
    else:
        name_part = voice_id

    # Remove common suffixes
    for suffix in ["Neural", "Multilingual", "Turbo"]:
        if name_part.endswith(suffix):
            name_part = name_part[:-len(suffix)]

    # Clean up any remaining hyphens or underscores
    name = name_part.strip("-_")

    return name if name else voice_id


def _book_title(reader: EpubReader) -> str:
    title_meta = reader.book.get_metadata("DC", "title")
    if title_meta:
        return title_meta[0][0]
    return reader.epub_path.stem


def _book_authors(reader: EpubReader) -> list[str]:
    authors = []
    for author, _attrs in reader.book.get_metadata("DC", "creator"):
        if author:
            authors.append(author)
    return authors




def _document_titles(reader: EpubReader) -> dict[str, str]:
    titles: dict[str, str] = {}
    for document in reader.iter_documents():
        tree = document.tree
        if tree is None:
            continue
        candidates = tree.xpath("//h1")
        title = ""
        if candidates:
            title = (candidates[0].text_content() or "").strip()
        if not title:
            title_nodes = tree.xpath("//title")
            if title_nodes:
                title = (title_nodes[0].text_content() or "").strip()
        titles[document.path.as_posix()] = title or document.path.stem
    return titles


def _find_cover_item(reader: EpubReader):
    for meta, attrs in reader.book.get_metadata("OPF", "meta"):
        if isinstance(attrs, dict) and attrs.get("name") == "cover":
            cover_id = attrs.get("content")
            if cover_id:
                item = reader.book.get_item_with_id(cover_id)
                if item:
                    return item
    spine_candidate = find_spine_cover_candidate(reader)
    if spine_candidate:
        try:
            return get_item_by_href(reader.book, spine_candidate.href)
        except KeyError:
            pass
    for item in reader.book.get_items():
        if item.get_type() == ITEM_IMAGE and "cover" in item.get_name().lower():
            return item
    for item in reader.book.get_items():
        if item.get_type() == ITEM_IMAGE:
            return item
    return None


def _generate_statement_audio(
    text: str,
    session: AudioSessionConfig,
    output_path: Path,
) -> Path | None:
    """Generate audio for opening/closing statement using the configured TTS engine.

    Matches the renderer.py workflow: generate TTS output, then convert to M4A.

    Args:
        text: Statement text to synthesize
        session: Audio session config with TTS provider and voice settings
        output_path: Where to save the M4A audio file

    Returns:
        Path to generated M4A audio file, or None if generation failed
    """
    if not text or not text.strip():
        return None

    try:
        # Use the same TTS engine as the main audiobook
        from .tts import create_tts_engine

        engine = create_tts_engine(
            provider=session.tts_provider,
            voice=session.voice,
            rate=None,  # Edge TTS only
            volume=None,  # Edge TTS only
            model=session.tts_model,
            speed=session.tts_speed,
        )

        # Determine temp file extension based on provider
        # OpenAI outputs AAC, Edge outputs MP3
        temp_ext = ".aac" if session.tts_provider == "openai" else ".mp3"
        temp_file = output_path.with_suffix(temp_ext)

        # Generate TTS output
        engine.synthesize(text.strip(), temp_file)

        # Always convert to M4A (matching renderer.py approach)
        audio = AudioSegment.from_file(temp_file)
        audio.export(
            output_path,
            format="mp4",
            codec="aac",
            parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
        )
        temp_file.unlink()  # Remove temporary file
        return output_path
    except Exception as exc:
        logger.warning("Failed to generate statement audio: %s", exc)
        return None


def _prepare_cover(
    output_root: Path,
    reader: EpubReader,
    explicit_cover: Path | None = None,
) -> Path | None:
    try:
        if explicit_cover:
            image = Image.open(explicit_cover)
            # Preserve original format if PNG
            original_format = image.format  # 'PNG', 'JPEG', etc.
        else:
            cover_item = _find_cover_item(reader)
            if not cover_item:
                return None
            image = Image.open(BytesIO(cover_item.get_content()))
            original_format = image.format
    except Exception:
        return None

    with image:
        # Only convert if necessary
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        width, height = image.size
        if width == 0 or height == 0:
            return None

        output_root.mkdir(parents=True, exist_ok=True)

        # Preserve PNG format for transparency, otherwise use JPEG
        if original_format == "PNG" and image.mode == "RGBA":
            cover_path = output_root / "cover.png"
            image.save(cover_path, format="PNG")
        else:
            # Convert RGBA to RGB for JPEG (no transparency support)
            if image.mode == "RGBA":
                image = image.convert("RGB")
            cover_path = output_root / "cover.jpg"
            image.save(cover_path, format="JPEG", quality=95)

        return cover_path


def _chapter_title(
    file_path: str,
    toc_map: dict[str, str],
    doc_titles: dict[str, str],
    custom_map: dict[str, str] | None = None,
) -> str:
    # Priority: custom > TOC > document title > filename
    if custom_map and file_path in custom_map:
        return custom_map[file_path]
    title = toc_map.get(file_path)
    if title:
        return title
    return doc_titles.get(file_path, Path(file_path).stem)


def _build_spine_to_toc_map(
    reader: EpubReader, toc_map: dict[str, str]
) -> dict[int, tuple[str, str]]:
    """Build mapping from spine index to (toc_file, toc_title).

    Maps each spine item to its governing TOC entry. Files between TOC entries
    are mapped to the previous TOC entry. Files before the first TOC entry or
    after the last TOC entry are not included in the map.

    Returns:
        Dict mapping spine_index -> (toc_file_path, toc_title)
    """
    # Build spine index lookup
    spine_items = reader.book.spine
    spine_lookup: dict[str, int] = {}
    for idx, (item_id, _linear) in enumerate(spine_items):
        item = reader.book.get_item_with_id(item_id)
        if item:
            spine_lookup[item.get_name()] = idx

    # Find spine indices for TOC entries
    toc_entries: list[tuple[int, str, str]] = []  # (spine_index, file_path, title)
    for file_path, title in toc_map.items():
        spine_idx = spine_lookup.get(file_path)
        if spine_idx is not None:
            toc_entries.append((spine_idx, file_path, title))

    if not toc_entries:
        # No TOC entries found, return empty map
        return {}

    # Sort by spine index
    toc_entries.sort(key=lambda x: x[0])

    # Build the mapping: spine_index -> (toc_file, toc_title)
    result: dict[int, tuple[str, str]] = {}

    # Get first and last TOC spine indices
    first_toc_idx = toc_entries[0][0]
    last_toc_idx = toc_entries[-1][0]

    # Map spine indices to their governing TOC entry
    current_toc_idx = 0
    for spine_idx in range(len(spine_items)):
        # Skip files before first TOC entry
        if spine_idx < first_toc_idx:
            continue

        # Skip files after last TOC entry
        if spine_idx > last_toc_idx:
            continue

        # Find the appropriate TOC entry for this spine index
        while (
            current_toc_idx < len(toc_entries) - 1
            and spine_idx >= toc_entries[current_toc_idx + 1][0]
        ):
            current_toc_idx += 1

        toc_spine_idx, toc_file, toc_title = toc_entries[current_toc_idx]
        result[spine_idx] = (toc_file, toc_title)

    return result


def group_segments_into_chapters(
    segments, spine_to_toc: dict
) -> list[tuple[str, list]]:
    """Group segments into chapters by TOC entry, falling back to file grouping.

    Shared by final assembly and the preview chapter export. They previously
    carried independent copies of this grouping and sort, so a preview could
    disagree with the book it was previewing.
    """
    chapter_map: dict[str, list] = {}
    for segment in segments:
        if spine_to_toc:
            toc_entry = spine_to_toc.get(segment.metadata.spine_index)
            if toc_entry is None:
                # Before the first TOC entry or after the last — not in any chapter.
                continue
            key = toc_entry[0]
        else:
            key = segment.file_path.as_posix()
        chapter_map.setdefault(key, []).append(segment)

    return sorted(
        chapter_map.items(),
        key=lambda item: (
            min(seg.metadata.spine_index for seg in item[1]),
            min(seg.metadata.order_in_file for seg in item[1]),
        ),
    )


def _load_custom_chapter_titles(settings: AppSettings) -> dict[str, str]:
    """Map segment file path -> custom chapter title from chapters.yaml, if present."""
    custom_chapters_map: dict[str, str] = {}
    chapters_yaml_path = settings.work_dir / "chapters.yaml"
    if not chapters_yaml_path.exists():
        return custom_chapters_map

    try:
        from .chapters import read_chapters_yaml

        chapters, _metadata = read_chapters_yaml(chapters_yaml_path)
        console.print("[cyan]Loading custom chapter titles from chapters.yaml[/cyan]")
        # Every segment file in a chapter inherits that chapter's title.
        for chapter in chapters:
            for seg_file in chapter.segments:
                custom_chapters_map[seg_file] = chapter.title
    except Exception as exc:
        logger.warning(f"Failed to load chapters.yaml: {exc}")
        console.print(f"[yellow]Warning: Could not load chapters.yaml: {exc}[/yellow]")

    return custom_chapters_map


def _render_statement(
    label: str,
    template: str | None,
    session: AudioSessionConfig,
    output_root: Path,
    book_title: str,
    author_str: str,
) -> Path | None:
    """Render one opening/closing statement to audio.

    The opening and closing paths were near-identical copies differing only in
    which setting they read and which words they logged.
    """
    if not template:
        return None

    text = template.format(
        book_name=book_title,
        author=author_str,
        narrator_name=_extract_narrator_name(session.voice),
    )
    audio_path = output_root / f"{label}_statement.m4a"
    try:
        if _generate_statement_audio(text, session, audio_path):
            console.print(f"[cyan]Generated {label} statement audio[/cyan]")
            return audio_path
    except Exception as exc:
        logger.warning("Failed to generate %s statement: %s", label, exc)
    return None


def _tag_audiobook(
    workspace_path: Path,
    book_title: str,
    authors: list[str],
    cover_path: Path | None,
    chapter_markers: list[tuple[int, str]],
) -> None:
    """Write title/author/cover metadata and chapter markers to the final file."""
    mp4 = MP4(workspace_path)
    mp4["©nam"] = [book_title]
    mp4["©alb"] = [book_title]
    if authors:
        mp4["©ART"] = [", ".join(authors)]
    if cover_path and cover_path.exists():
        cover_bytes = cover_path.read_bytes()
        cover_img_format = (
            MP4Cover.FORMAT_PNG if cover_path.suffix.lower() == ".png" else MP4Cover.FORMAT_JPEG
        )
        mp4["covr"] = [MP4Cover(cover_bytes, imageformat=cover_img_format)]

    native_chapters = False
    if MP4Chapter:
        chapters = [MP4Chapter(start_ms, title=title) for start_ms, title in chapter_markers]
        if chapters and hasattr(mp4, "chapters"):
            mp4.chapters = chapters
            native_chapters = True
    mp4.save()

    # Fall back to manual chpl injection when mutagen cannot write chapters natively.
    if not native_chapters and chapter_markers:
        try:
            write_chapter_markers(workspace_path, chapter_markers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chapter marker injection failed: %s", exc)


def _concat_entry(path: Path) -> str:
    """Format one line of an ffmpeg concat demuxer list.

    The demuxer treats a single quote as the string terminator, so a path
    containing an apostrophe (common in book titles) truncated the filename and
    ffmpeg failed on a perfectly valid workspace. The documented escape is to
    close the quote, emit an escaped quote, and reopen it.
    """
    escaped = str(path.absolute()).replace("'", "'\\''")
    return f"file '{escaped}'\n"


def chapter_is_current(chapter_path: Path, segments: list[Segment], audio_state) -> bool:
    """True when a cached chapter is newer than, and built from, these segments.

    Existence alone is not enough: re-synthesising with a different voice,
    speed or source text rewrites the segment audio but leaves the chapter
    file in place, so the old audio was served indefinitely.

    Timestamps alone are not enough either. If a chapter was built from
    segments A+B and a later run includes only A, every remaining source file
    can be older than the chapter, so the stale chapter — still containing B —
    was reused. The composition is recorded alongside the audio and compared.
    """
    if not chapter_path.exists():
        return False
    manifest_path = chapter_path.with_suffix(chapter_path.suffix + ".segments")
    expected = "\n".join(segment.segment_id for segment in segments)
    try:
        if manifest_path.read_text(encoding="utf-8") != expected:
            return False
    except OSError:
        # No manifest: written by an older version, composition unknown.
        return False
    chapter_mtime = chapter_path.stat().st_mtime
    for segment in segments:
        seg_state = audio_state.segments.get(segment.segment_id)
        if not seg_state or not seg_state.audio_path:
            continue
        source = Path(seg_state.audio_path)
        if source.exists() and source.stat().st_mtime > chapter_mtime:
            return False
    return True


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
                duration = _get_audio_duration(chapter_path)
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
            for title, chapter_path, file_path, segments in expected_chapters:
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

                # Build concat list with segments and silence
                concat_list_path = chapter_temp_dir / "concat_list.txt"

                with open(concat_list_path, "w", encoding="utf-8") as f:
                    for idx, segment_path in enumerate(available_segment_paths):
                        f.write(_concat_entry(segment_path))

                        # Add pause between segments (except after last segment)
                        if idx < len(available_segment_paths) - 1:
                            pause_seconds = rng.uniform(*segment_pause_range)
                            pause_path = chapter_temp_dir / f"pause_{idx}.m4a"
                            pause_silence = AudioSegment.silent(duration=int(pause_seconds * 1000))
                            pause_silence.export(
                                pause_path,
                                format="mp4",
                                codec="aac",
                                parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
                            )
                            f.write(_concat_entry(pause_path))

                # Add chapter gap at the end (except for last chapter)
                index = expected_chapters.index((title, chapter_path, file_path, segments)) + 1
                if index < len(sorted_chapters):
                    chapter_gap_rng = random.Random(0xA10D10 + index)
                    chapter_gap_seconds = chapter_gap_rng.uniform(2.0, 4.0)
                    gap_path = chapter_temp_dir / "chapter_gap.m4a"
                    gap_silence = AudioSegment.silent(duration=int(chapter_gap_seconds * 1000))
                    gap_silence.export(
                        gap_path,
                        format="mp4",
                        codec="aac",
                        parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
                    )
                    with open(concat_list_path, "a", encoding="utf-8") as f:
                        f.write(_concat_entry(gap_path))

                # Use ffmpeg to concatenate M4A files without re-encoding
                subprocess.run(
                    [
                        "ffmpeg",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", str(concat_list_path),
                        "-vn",  # Ignore video streams (cover art)
                        "-c:a", "copy",
                        "-y",
                        str(chapter_path),
                    ],
                    check=True,
                    capture_output=True,
                )

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
                    actual_duration = _get_audio_duration(chapter_path)
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

    # Create concat file list for ffmpeg
    concat_file = audiobook_dir / "concat_list.txt"
    chapter_markers: list[tuple[int, str]] = []
    current_position_seconds = 0.0  # Use float for precision

    with open(concat_file, "w", encoding="utf-8") as f:
        # Add opening statement if available
        if opening_audio_path and opening_audio_path.exists():
            f.write(f"file '{opening_audio_path.absolute()}'\n")

            # Add silence after opening
            opening_silence_path = audiobook_dir / "opening_silence.m4a"
            planned_silence_duration = random.Random(0xDEADBEEF).uniform(2.0, 4.0)
            opening_silence = AudioSegment.silent(duration=int(planned_silence_duration * 1000))
            opening_silence.export(
                opening_silence_path,
                format="mp4",
                codec="aac",
                parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
            )
            f.write(f"file '{opening_silence_path.absolute()}'\n")

            # Read actual durations from files using ffprobe (accurate for M4A)
            try:
                opening_duration_seconds = _get_audio_duration(opening_audio_path)
                actual_silence_duration = _get_audio_duration(opening_silence_path)

                current_position_seconds += opening_duration_seconds + actual_silence_duration
            except Exception as exc:
                logger.warning(f"Could not load opening statement duration, chapter timestamps may be offset: {exc}")

        for idx, (title, chapter_path, duration_seconds) in enumerate(chapter_audios):
            safe_title = title.strip() if isinstance(title, str) else ""
            if not safe_title:
                safe_title = f"Chapter {idx + 1}"

            # Record chapter marker at current position (convert to ms only here)
            chapter_markers.append((int(current_position_seconds * 1000), safe_title))

            # Add chapter file to concat list
            f.write(f"file '{chapter_path.absolute()}'\n")

            # Update position with chapter duration (already includes silence at end)
            current_position_seconds += duration_seconds

        # Add closing statement if available
        if closing_audio_path and closing_audio_path.exists():
            # Add 2-4 seconds silence before closing
            # (This silence is added as a separate silent M4A)
            silence_path = audiobook_dir / "closing_silence.m4a"
            silence_duration = random.Random(0xC105ED).uniform(2.0, 4.0)
            silence_segment = AudioSegment.silent(duration=int(silence_duration * 1000))
            silence_segment.export(
                silence_path,
                format="mp4",
                codec="aac",
                parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
            )
            f.write(f"file '{silence_path.absolute()}'\n")

            f.write(f"file '{closing_audio_path.absolute()}'\n")

    # Use ffmpeg to concatenate M4A files without re-encoding
    # All input files are already M4A/AAC, so we can use -c:a copy for instant concat
    console.print("[cyan]Creating final M4A audiobook…[/cyan]")
    subprocess.run(
        [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-vn",  # Ignore video streams (cover art)
            "-c:a", "copy",  # Copy audio stream without re-encoding (instant!)
            "-y",  # Overwrite output file
            str(workspace_path),
        ],
        check=True,
        capture_output=True,
    )

    # Clean up concat file
    concat_file.unlink()

    # Clean up statement audio files (already included in final audiobook)
    if opening_audio_path and opening_audio_path.exists():
        opening_audio_path.unlink()
        opening_silence_path = audiobook_dir / "opening_silence.m4a"
        if opening_silence_path.exists():
            opening_silence_path.unlink()

    if closing_audio_path and closing_audio_path.exists():
        closing_audio_path.unlink()
        closing_silence_path = audiobook_dir / "closing_silence.m4a"
        if closing_silence_path.exists():
            closing_silence_path.unlink()

    _tag_audiobook(workspace_path, book_title, authors, cover_path, chapter_markers)

    # Keep audiobook in work_dir structure instead of moving to EPUB parent
    return workspace_path
