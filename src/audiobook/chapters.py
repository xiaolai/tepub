"""Chapter management utilities for audiobooks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from mutagen.mp4 import MP4

from config import AppSettings
from epub_io.reader import EpubReader
from epub_io.toc_utils import parse_toc_to_dict
from state.models import Segment
from state.store import load_segments

from .mp4chapters import write_chapter_markers


def _parse_timestamp(value: str | int | float) -> float:
    """Parse timestamp to seconds.

    Accepts:
    - "1:23:45" or "01:23:45" -> 5025.0 seconds (h:mm:ss)
    - "1:9:2" -> 4142.0 seconds (flexible, no zero-padding required)
    - "23:45" -> 1425.0 seconds (mm:ss, assumes no hours)
    - "45" -> 45.0 seconds (ss only)
    - 5025.0 -> 5025.0 (numeric seconds, backward compat)

    Returns:
        float: Timestamp in seconds
    """
    if isinstance(value, (int, float)):
        return float(value)

    parts = str(value).split(":")
    if len(parts) == 3:  # h:mm:ss
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:  # mm:ss
        return int(parts[0]) * 60 + int(parts[1])
    else:  # ss
        return float(value)


def _format_timestamp(seconds: float) -> str:
    """Format seconds as h:mm:ss string with zero-padding.

    Examples:
    - 5025.0 -> "1:23:45"
    - 1425.0 -> "0:23:45"
    - 45.5 -> "0:00:45"

    Args:
        seconds: Timestamp in seconds

    Returns:
        str: Formatted timestamp as "h:mm:ss"
    """
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"


class ChapterInfo:
    """Chapter information with optional timestamp."""

    def __init__(self, title: str, start: float | None = None, segments: list[str] | None = None):
        self.title = title
        self.start = start  # seconds, None if not yet generated
        self.segments = segments or []  # file paths for preview mode

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML export."""
        result = {"title": self.title}
        if self.start is not None:
            result["start"] = _format_timestamp(self.start)
        if self.segments:
            result["segments"] = self.segments
        return result

    @staticmethod
    def from_dict(data: dict) -> ChapterInfo:
        """Create from dictionary loaded from YAML."""
        return ChapterInfo(
            title=data["title"],
            start=_parse_timestamp(data["start"]) if "start" in data else None,
            segments=data.get("segments", []),
        )


def extract_chapters_from_epub(
    input_epub: Path, settings: AppSettings
) -> tuple[list[ChapterInfo], dict]:
    """Extract chapter structure from EPUB (preview mode, no timestamps).

    Returns:
        Tuple of (chapters list, metadata dict)
    """
    reader = EpubReader(input_epub, settings)
    toc_map = parse_toc_to_dict(reader)
    segments_doc = load_segments(settings.segments_file)

    # Build spine to TOC mapping (reuse logic from assembly.py)
    from .assembly import _build_spine_to_toc_map, _document_titles

    spine_to_toc = _build_spine_to_toc_map(reader, toc_map)
    doc_titles = _document_titles(reader)

    # Group segments by chapter
    chapter_map: dict[str, list[Segment]] = {}
    for segment in segments_doc.segments:
        if spine_to_toc:
            spine_idx = segment.metadata.spine_index
            toc_entry = spine_to_toc.get(spine_idx)
            if toc_entry is None:
                continue
            toc_file, toc_title = toc_entry
            key = toc_file
        else:
            # Fallback to file-based grouping
            key = segment.file_path.as_posix()

        if key not in chapter_map:
            chapter_map[key] = []
        chapter_map[key].append(segment)

    # Sort chapters by spine order
    sorted_chapters = sorted(
        chapter_map.items(),
        key=lambda item: (
            min(seg.metadata.spine_index for seg in item[1]),
            min(seg.metadata.order_in_file for seg in item[1]),
        ),
    )

    # Build chapter info list
    chapters = []
    for file_path, segments in sorted_chapters:
        # Determine title
        if toc_map and file_path in toc_map:
            title = toc_map[file_path]
        else:
            title = doc_titles.get(file_path, Path(file_path).stem)

        # Collect segment file paths for this chapter
        segment_files = sorted(
            set(seg.file_path.as_posix() for seg in segments),
            key=lambda f: min(
                seg.metadata.spine_index
                for seg in segments
                if seg.file_path.as_posix() == f
            ),
        )

        chapters.append(ChapterInfo(title=title, start=None, segments=segment_files))

    # Build metadata
    metadata = {
        "source": str(input_epub),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "preview",
        "note": "Timestamps will be determined during audiobook generation",
    }

    return chapters, metadata


def extract_chapters_from_mp4(mp4_path: Path) -> tuple[list[ChapterInfo], dict]:
    """Extract chapter information from existing M4A audiobook.

    Returns:
        Tuple of (chapters list, metadata dict)
    """
    mp4 = MP4(mp4_path)

    if not mp4.chapters:
        raise ValueError(f"No chapter information found in {mp4_path}")

    chapters = []
    for chapter in mp4.chapters:
        # mutagen.mp4.Chapter.start is already in seconds (not milliseconds)
        start_seconds = chapter.start
        chapters.append(ChapterInfo(title=chapter.title, start=start_seconds))

    # Get duration
    duration = mp4.info.length if hasattr(mp4.info, "length") else None

    metadata = {
        "source": str(mp4_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "from_audiobook",
        "duration": round(duration, 3) if duration else None,
    }

    return chapters, metadata


def write_chapters_yaml(
    chapters: list[ChapterInfo], metadata: dict, output_path: Path
) -> None:
    """Write chapters to YAML config file."""
    data = {
        "# Chapter information": None,
        "metadata": metadata,
        "chapters": [ch.to_dict() for ch in chapters],
    }

    # Custom YAML formatting
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        # Write header comment
        f.write(f"# Chapter information for audiobook\n")
        f.write(f"# Source: {metadata.get('source', 'unknown')}\n")
        f.write(f"# Generated: {metadata.get('generated_at', 'unknown')}\n")
        if metadata.get("mode") == "preview":
            f.write(f"# {metadata.get('note', '')}\n")
        f.write("\n")

        # Write metadata
        f.write("metadata:\n")
        for key, value in metadata.items():
            if value is not None:
                if isinstance(value, str):
                    f.write(f'  {key}: "{value}"\n')
                else:
                    f.write(f"  {key}: {value}\n")
        f.write("\n")

        # Write chapters
        f.write("chapters:\n")
        for ch in chapters:
            f.write(f'  - title: "{ch.title}"\n')
            if ch.start is not None:
                f.write(f'    start: "{_format_timestamp(ch.start)}"\n')
            if ch.segments:
                f.write(f"    # Segments: {', '.join(ch.segments[:3])}")
                if len(ch.segments) > 3:
                    f.write(f" (and {len(ch.segments) - 3} more)")
                f.write("\n")


def read_chapters_yaml(yaml_path: Path) -> tuple[list[ChapterInfo], dict]:
    """Read chapters from YAML config file.

    Returns:
        Tuple of (chapters list, metadata dict)
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "chapters" not in data:
        raise ValueError(f"Invalid chapters YAML file: {yaml_path}")

    chapters = [ChapterInfo.from_dict(ch) for ch in data["chapters"]]
    metadata = data.get("metadata", {})

    return chapters, metadata


def validate_chapters(
    chapters: list[ChapterInfo], duration: float | None = None
) -> list[str]:
    """Validate chapter data.

    Args:
        chapters: List of chapter info
        duration: Optional audiobook duration to validate against

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    if not chapters:
        errors.append("No chapters defined")
        return errors

    # Check for valid titles
    for i, ch in enumerate(chapters):
        if not ch.title or not ch.title.strip():
            errors.append(f"Chapter {i + 1}: Empty title")

    # If timestamps exist, validate them
    has_timestamps = any(ch.start is not None for ch in chapters)
    if has_timestamps:
        # Check all have timestamps
        missing = [i + 1 for i, ch in enumerate(chapters) if ch.start is None]
        if missing:
            errors.append(f"Chapters {missing} missing timestamps")

        # Check timestamps are in order and non-negative
        for i, ch in enumerate(chapters):
            if ch.start is None:
                continue
            if ch.start < 0:
                errors.append(f"Chapter {i + 1}: Negative timestamp {ch.start}")
            if i > 0 and chapters[i - 1].start is not None:
                if ch.start <= chapters[i - 1].start:
                    errors.append(
                        f"Chapter {i + 1}: Timestamp {ch.start} not after previous chapter"
                    )

        # Check against duration
        if duration is not None:
            for i, ch in enumerate(chapters):
                if ch.start is not None and ch.start > duration:
                    errors.append(
                        f"Chapter {i + 1}: Timestamp {ch.start}s exceeds audiobook duration {duration}s"
                    )

    return errors


def update_mp4_chapters(mp4_path: Path, chapters: list[ChapterInfo]) -> None:
    """Update M4A audiobook with new chapter markers.

    Args:
        mp4_path: Path to M4A audiobook file
        chapters: List of chapter info with timestamps
    """
    # Validate all chapters have timestamps
    if any(ch.start is None for ch in chapters):
        raise ValueError("All chapters must have timestamps to update audiobook")

    # Get audiobook duration for validation
    mp4 = MP4(mp4_path)
    duration = mp4.info.length if hasattr(mp4.info, "length") else None

    # Validate chapters
    errors = validate_chapters(chapters, duration)
    if errors:
        raise ValueError(f"Chapter validation failed:\n" + "\n".join(errors))

    # Convert to markers format (milliseconds, title)
    markers = [(int(ch.start * 1000), ch.title) for ch in chapters]

    # Write chapter markers using existing functionality
    write_chapter_markers(mp4_path, markers)
