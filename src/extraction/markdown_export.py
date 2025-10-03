from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import html2text
from lxml import html as lxml_html

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.resources import iter_spine_items
from epub_io.toc_utils import parse_toc_to_dict
from epub_io.path_utils import normalize_epub_href
from state.models import Segment
from state.store import load_segments

console = get_console()


@dataclass
class ChapterBlock:
    """Represents a logical chapter grouping multiple spine files."""

    title: str  # TOC entry title
    spine_start: int  # First spine index in this block
    spine_end: int  # Last spine index (exclusive) - start of next block
    files: list[Path]  # All file paths in this block, sorted by spine
    toc_href: str  # Original TOC href for reference




def _build_chapter_blocks(
    reader: EpubReader, segments_by_file: dict[Path, list[Segment]]
) -> list[ChapterBlock]:
    """
    Group spine files into chapter blocks based on TOC structure.

    Smart grouping logic:
    - If multiple spine files exist between two TOC entries, group them
    - If one TOC entry = one spine file, keep as single block
    - Files before first TOC entry are kept as individual blocks
    - Files after last TOC entry are grouped as one block

    Args:
        reader: EPUB reader with access to TOC and spine
        segments_by_file: Segments grouped by file path

    Returns:
        List of ChapterBlock objects, one per logical chapter/section
    """
    # Build spine index lookup: Path -> spine_index
    spine_lookup: dict[Path, int] = {}
    for spine_item in iter_spine_items(reader.book):
        spine_lookup[spine_item.href] = spine_item.index

    # Get max spine index
    max_spine_index = max(spine_lookup.values()) if spine_lookup else 0

    # Extract TOC entries with their spine indices
    toc_entries: list[tuple[str, int, str]] = []  # (title, spine_index, href)

    def collect_toc_entries(entries):
        for item in entries:
            if hasattr(item, "href") and hasattr(item, "title"):
                href = item.href.split("#", 1)[0]
                href_path = Path(href)
                if href_path in spine_lookup:
                    spine_idx = spine_lookup[href_path]
                    title = item.title or href
                    toc_entries.append((title, spine_idx, href))
            elif isinstance(item, (list, tuple)) and item:
                head = item[0] if item else None
                if head and hasattr(head, "href") and hasattr(head, "title"):
                    href = head.href.split("#", 1)[0]
                    href_path = Path(href)
                    if href_path in spine_lookup:
                        spine_idx = spine_lookup[href_path]
                        title = head.title or href
                        toc_entries.append((title, spine_idx, href))
                if len(item) > 1:
                    collect_toc_entries(item[1])

    toc = reader.book.toc or []
    collect_toc_entries(toc)

    # Sort by spine index
    toc_entries.sort(key=lambda x: x[1])

    # Build chapter blocks
    blocks: list[ChapterBlock] = []

    for i, (title, spine_start, href) in enumerate(toc_entries):
        # Determine end of this block (start of next TOC entry or end of spine)
        if i + 1 < len(toc_entries):
            spine_end = toc_entries[i + 1][1]
        else:
            spine_end = max_spine_index + 1

        # Collect all files in spine range [spine_start, spine_end)
        block_files: list[tuple[Path, int]] = []  # (path, spine_index)
        for file_path, segments in segments_by_file.items():
            if segments:
                file_spine_idx = segments[0].metadata.spine_index
                if spine_start <= file_spine_idx < spine_end:
                    block_files.append((file_path, file_spine_idx))

        # Sort by spine index
        block_files.sort(key=lambda x: x[1])
        files_only = [path for path, _ in block_files]

        if files_only:
            blocks.append(
                ChapterBlock(
                    title=title,
                    spine_start=spine_start,
                    spine_end=spine_end,
                    files=files_only,
                    toc_href=href,
                )
            )

    # Handle files before first TOC entry (front matter)
    if toc_entries and segments_by_file:
        first_toc_spine = min(entry[1] for entry in toc_entries)
        front_matter_files: list[tuple[Path, int]] = []

        for file_path, segments in segments_by_file.items():
            if segments:
                file_spine_idx = segments[0].metadata.spine_index
                if file_spine_idx < first_toc_spine:
                    front_matter_files.append((file_path, file_spine_idx))

        if front_matter_files:
            # Sort and add as individual blocks (they don't belong to a chapter)
            front_matter_files.sort(key=lambda x: x[1])
            for file_path, spine_idx in front_matter_files:
                # Use filename as title for front matter
                title = file_path.stem.replace("_", " ").title()
                blocks.insert(
                    0,
                    ChapterBlock(
                        title=title,
                        spine_start=spine_idx,
                        spine_end=spine_idx + 1,
                        files=[file_path],
                        toc_href=file_path.as_posix(),
                    ),
                )

    # Sort all blocks by spine index
    blocks.sort(key=lambda b: b.spine_start)

    return blocks


def _sanitize_filename(title: str, max_length: int = 50) -> str:
    """Convert title to safe filename component."""
    # Remove or replace unsafe characters
    safe = re.sub(r'[<>:"/\\|?*!]', "", title)
    safe = re.sub(r"\s+", "-", safe.strip())
    safe = safe.lower()
    # Remove leading/trailing hyphens
    safe = safe.strip("-")
    # Limit length
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip("-")
    return safe or "untitled"




def _html_to_markdown(
    html_content: str,
    document_path: Path,
    image_mapping: dict[str, str],
) -> str:
    """
    Convert HTML content to markdown, preserving formatting and images.

    Args:
        html_content: HTML content to convert
        document_path: Path of the document containing this content (for resolving image refs)
        image_mapping: Mapping from EPUB image paths to extracted filenames

    Returns:
        Markdown formatted text with image references
    """
    try:
        # Configure html2text
        h = html2text.HTML2Text()
        h.body_width = 0  # Don't wrap lines
        h.ignore_links = False  # Preserve links
        h.ignore_images = False  # Preserve images
        h.ignore_emphasis = False  # Preserve bold/italic
        h.mark_code = True  # Mark code blocks
        h.protect_links = True  # Don't alter link text
        h.single_line_break = False  # Use double line breaks for paragraphs

        # Convert HTML to markdown
        markdown = h.handle(html_content)

        # Post-process: fix image paths to use images/ directory
        # Parse to find image references and replace with correct paths
        tree = lxml_html.fromstring(f"<div>{html_content}</div>")
        for img in tree.xpath(".//img | .//image"):
            src = img.get("src") or img.get("href") or img.get("{http://www.w3.org/1999/xlink}href")
            if src:
                normalized_path = normalize_epub_href(document_path, src)
                if normalized_path and normalized_path in image_mapping:
                    extracted_name = image_mapping[normalized_path]
                    # Replace the path in markdown
                    # html2text converts <img src="path"> to ![alt](path)
                    markdown = markdown.replace(f"]({src})", f"](images/{extracted_name})")
                    # Also handle URL-encoded or relative variations
                    markdown = markdown.replace(
                        f"]({normalized_path})", f"](images/{extracted_name})"
                    )

        return markdown.strip()
    except Exception as e:
        # Fallback to simple tag stripping
        console.print(f"[yellow]Warning: HTML to markdown conversion failed: {e}[/yellow]")
        return re.sub(r"<[^>]+>", "", html_content)


def export_to_markdown(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
    image_mapping: dict[str, str] | None = None,
) -> list[Path]:
    """
    Export extracted segments to numbered markdown files.

    Uses smart chapter grouping: multiple spine files between TOC entries
    are combined into a single markdown file per chapter.

    Args:
        settings: Application settings
        input_epub: Path to source EPUB
        output_dir: Directory where markdown files will be written
        image_mapping: Optional mapping from EPUB image paths to extracted filenames

    Returns:
        List of created markdown file paths
    """
    segments_doc = load_segments(settings.segments_file)
    reader = EpubReader(input_epub, settings)
    img_map = image_mapping or {}

    # Group segments by file path
    by_file: dict[Path, list[Segment]] = {}
    for segment in segments_doc.segments:
        if segment.file_path not in by_file:
            by_file[segment.file_path] = []
        by_file[segment.file_path].append(segment)

    # Sort segments within each file
    for file_path in by_file:
        by_file[file_path].sort(key=lambda s: s.metadata.order_in_file)

    # Build chapter blocks for smart grouping
    blocks = _build_chapter_blocks(reader, by_file)

    output_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []

    for idx, block in enumerate(blocks, start=1):
        # Use chapter title for filename
        safe_title = _sanitize_filename(block.title)
        md_filename = f"{idx:03d}_{safe_title}.md"
        md_path = output_dir / md_filename

        # Build markdown content from all files in this block
        lines = [f"# {block.title}", ""]

        # Process all files in the block
        for file_path in block.files:
            segments = by_file.get(file_path, [])
            for segment in segments:
                content = segment.source_content or ""
                if not content.strip():
                    continue

                # Convert HTML to markdown, preserving images
                text = _html_to_markdown(content, file_path, img_map)
                if text.strip():
                    lines.append(text)
                    lines.append("")

        # Write file
        md_content = "\n".join(lines)
        md_path.write_text(md_content, encoding="utf-8")
        created_files.append(md_path)

    return created_files


def export_combined_markdown(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
    image_mapping: dict[str, str] | None = None,
) -> Path:
    """
    Export all segments to a single combined markdown file.

    Uses smart chapter grouping: multiple spine files between TOC entries
    are combined under a single ## heading per chapter.

    Args:
        settings: Application settings
        input_epub: Path to source EPUB
        output_dir: Directory where markdown file will be written
        image_mapping: Optional mapping from EPUB image paths to extracted filenames

    Returns:
        Path to the created combined markdown file
    """
    segments_doc = load_segments(settings.segments_file)
    reader = EpubReader(input_epub, settings)
    img_map = image_mapping or {}

    # Group segments by file path
    by_file: dict[Path, list[Segment]] = {}
    for segment in segments_doc.segments:
        if segment.file_path not in by_file:
            by_file[segment.file_path] = []
        by_file[segment.file_path].append(segment)

    # Sort segments within each file
    for file_path in by_file:
        by_file[file_path].sort(key=lambda s: s.metadata.order_in_file)

    # Build chapter blocks for smart grouping
    blocks = _build_chapter_blocks(reader, by_file)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Use EPUB filename (without extension) for combined markdown
    combined_filename = f"{input_epub.stem}.md"
    combined_path = output_dir / combined_filename

    # Build combined content
    all_lines = []

    # Add book title
    book_title = input_epub.stem
    all_lines.append(f"# {book_title}")
    all_lines.append("")
    all_lines.append("---")
    all_lines.append("")

    # Add each chapter block
    for idx, block in enumerate(blocks, start=1):
        # Add chapter heading (one per block, not per file)
        all_lines.append(f"## {block.title}")
        all_lines.append("")

        # Add content from all files in this block
        for file_path in block.files:
            segments = by_file.get(file_path, [])
            for segment in segments:
                content = segment.source_content or ""
                if not content.strip():
                    continue

                # Convert HTML to markdown, preserving images
                text = _html_to_markdown(content, file_path, img_map)
                if text.strip():
                    all_lines.append(text)
                    all_lines.append("")

        # Add separator between chapters (except after last chapter)
        if idx < len(blocks):
            all_lines.append("---")
            all_lines.append("")

    # Write combined file
    combined_content = "\n".join(all_lines)
    combined_path.write_text(combined_content, encoding="utf-8")

    return combined_path
