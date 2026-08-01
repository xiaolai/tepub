from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import html2text
from lxml import html as lxml_html

from config import AppSettings
from console_singleton import get_console
from epub_io.path_utils import normalize_epub_href
from epub_io.reader import EpubReader
from epub_io.resources import iter_spine_items
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




TocEntry = tuple[str, int, str]  # (title, spine_index, href)


def _build_spine_lookup(reader: EpubReader) -> dict[Path, int]:
    """Map each spine document path to its spine index."""
    return {item.href: item.index for item in iter_spine_items(reader.book)}


def _collect_toc_entries(toc, spine_lookup: dict[Path, int]) -> list[TocEntry]:
    """Flatten the (possibly nested) TOC into entries that resolve to spine files."""
    entries: list[TocEntry] = []

    def _append_if_in_spine(node) -> None:
        href = node.href.split("#", 1)[0]
        href_path = Path(href)
        if href_path in spine_lookup:
            entries.append((node.title or href, spine_lookup[href_path], href))

    def _walk(items) -> None:
        for item in items:
            if hasattr(item, "href") and hasattr(item, "title"):
                _append_if_in_spine(item)
            elif isinstance(item, (list, tuple)) and item:
                head = item[0]
                if head is not None and hasattr(head, "href") and hasattr(head, "title"):
                    _append_if_in_spine(head)
                if len(item) > 1:
                    _walk(item[1])

    _walk(toc)
    return entries


def _dedupe_entries_by_spine_index(entries: list[TocEntry]) -> list[TocEntry]:
    """Keep the first TOC entry per spine index.

    Fragment hrefs ("ch1.xhtml#part-2") are stripped to the file, so several TOC
    entries can share one spine index. Left as-is they produce zero-length ranges
    [n, n): every such entry but the last contributes no files, and the last
    silently absorbs the whole file. The first entry is the one whose title
    introduces that file.
    """
    deduped: list[TocEntry] = []
    seen: set[int] = set()
    for entry in entries:
        if entry[1] in seen:
            continue
        seen.add(entry[1])
        deduped.append(entry)
    return deduped


def _files_with_spine_index(
    segments_by_file: dict[Path, list[Segment]],
) -> list[tuple[Path, int]]:
    """Every file that has segments, paired with its spine index, spine-ordered."""
    pairs = [
        (file_path, segments[0].metadata.spine_index)
        for file_path, segments in segments_by_file.items()
        if segments
    ]
    pairs.sort(key=lambda item: item[1])
    return pairs


def _single_file_block(file_path: Path, spine_idx: int) -> ChapterBlock:
    """A one-file block titled from the filename (front matter / TOC-less fallback)."""
    return ChapterBlock(
        title=file_path.stem.replace("_", " ").title(),
        spine_start=spine_idx,
        spine_end=spine_idx + 1,
        files=[file_path],
        toc_href=file_path.as_posix(),
    )


def _blocks_from_toc_ranges(
    toc_entries: list[TocEntry],
    segments_by_file: dict[Path, list[Segment]],
    max_spine_index: int,
) -> list[ChapterBlock]:
    """Build one block per TOC entry, spanning [this entry, next entry)."""
    files = _files_with_spine_index(segments_by_file)
    blocks: list[ChapterBlock] = []

    for i, (title, spine_start, href) in enumerate(toc_entries):
        spine_end = (
            toc_entries[i + 1][1] if i + 1 < len(toc_entries) else max_spine_index + 1
        )
        block_files = [
            file_path for file_path, idx in files if spine_start <= idx < spine_end
        ]
        if block_files:
            blocks.append(
                ChapterBlock(
                    title=title,
                    spine_start=spine_start,
                    spine_end=spine_end,
                    files=block_files,
                    toc_href=href,
                )
            )
    return blocks


def _front_matter_blocks(
    toc_entries: list[TocEntry],
    segments_by_file: dict[Path, list[Segment]],
) -> list[ChapterBlock]:
    """One block per file appearing before the first TOC entry."""
    if not toc_entries or not segments_by_file:
        return []
    first_toc_spine = min(entry[1] for entry in toc_entries)
    return [
        _single_file_block(file_path, idx)
        for file_path, idx in _files_with_spine_index(segments_by_file)
        if idx < first_toc_spine
    ]


def _spine_ordered_blocks(
    segments_by_file: dict[Path, list[Segment]],
) -> list[ChapterBlock]:
    """One block per file in spine order, used when the TOC yields nothing."""
    return [
        _single_file_block(file_path, idx)
        for file_path, idx in _files_with_spine_index(segments_by_file)
    ]


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
    spine_lookup = _build_spine_lookup(reader)
    max_spine_index = max(spine_lookup.values()) if spine_lookup else 0

    toc_entries = _collect_toc_entries(reader.book.toc or [], spine_lookup)
    toc_entries.sort(key=lambda x: x[1])
    toc_entries = _dedupe_entries_by_spine_index(toc_entries)

    blocks = _blocks_from_toc_ranges(toc_entries, segments_by_file, max_spine_index)
    blocks = _front_matter_blocks(toc_entries, segments_by_file) + blocks

    # Fall back to spine order when the TOC is missing or none of its entries
    # matched the spine. Without this an EPUB with an unusable TOC exported
    # nothing at all: per-chapter export wrote no files and combined export
    # omitted every segment.
    if not blocks:
        blocks = _spine_ordered_blocks(segments_by_file)

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
                # Resolve against the *file* the reference points at: image_mapping is
                # keyed by clean EPUB paths, so "fig.png?v=2" or "icons.svg#star" never
                # matched and the image silently kept its original path. Strip the
                # query/fragment and percent-decode here rather than in
                # normalize_epub_href, which deliberately preserves both for document
                # links (see tests/epub_io/test_path_utils.py).
                lookup_src = unquote(src.split("#", 1)[0].split("?", 1)[0])
                normalized_path = normalize_epub_href(document_path, lookup_src)
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
        # Fall back to parser-based text extraction. The previous regex fallback
        # (`<[^>]+>` -> "") deleted any legitimate text containing angle brackets
        # and left HTML entities unresolved, silently corrupting the export.
        console.print(f"[yellow]Warning: HTML to markdown conversion failed: {e}[/yellow]")
        try:
            fallback_tree = lxml_html.fromstring(f"<div>{html_content}</div>")
            return fallback_tree.text_content().strip()
        except Exception:
            # Content is not parseable as HTML at all; return it unchanged rather
            # than mangling it with a regex.
            return html_content.strip()


def _prepare_export(
    settings: AppSettings,
    input_epub: Path,
    image_mapping: dict[str, str] | None,
) -> tuple[dict[Path, list[Segment]], list[ChapterBlock], dict[str, str]]:
    """Load segments, group them by file, and build chapter blocks.

    Shared by both exporters, which previously carried identical copies of this
    setup and could drift apart.
    """
    segments_doc = load_segments(settings.segments_file)
    reader = EpubReader(input_epub, settings)
    img_map = image_mapping or {}

    # Group segments by file path
    by_file: dict[Path, list[Segment]] = {}
    for segment in segments_doc.segments:
        by_file.setdefault(segment.file_path, []).append(segment)

    # Sort segments within each file
    for file_path in by_file:
        by_file[file_path].sort(key=lambda s: s.metadata.order_in_file)

    blocks = _build_chapter_blocks(reader, by_file)
    return by_file, blocks, img_map


def _render_block_body(
    block: ChapterBlock,
    by_file: dict[Path, list[Segment]],
    img_map: dict[str, str],
) -> list[str]:
    """Render one chapter block's segments to markdown lines (no heading)."""
    lines: list[str] = []
    for file_path in block.files:
        for segment in by_file.get(file_path, []):
            content = segment.source_content or ""
            if not content.strip():
                continue

            # Convert HTML to markdown, preserving images
            text = _html_to_markdown(content, file_path, img_map)
            if text.strip():
                lines.append(text)
                lines.append("")
    return lines


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
    by_file, blocks, img_map = _prepare_export(settings, input_epub, image_mapping)

    output_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []

    for idx, block in enumerate(blocks, start=1):
        # Use chapter title for filename
        safe_title = _sanitize_filename(block.title)
        md_filename = f"{idx:03d}_{safe_title}.md"
        md_path = output_dir / md_filename

        # Build markdown content from all files in this block
        lines = [f"# {block.title}", ""]
        lines.extend(_render_block_body(block, by_file, img_map))

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
    by_file, blocks, img_map = _prepare_export(settings, input_epub, image_mapping)

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
        all_lines.extend(_render_block_body(block, by_file, img_map))

        # Add separator between chapters (except after last chapter)
        if idx < len(blocks):
            all_lines.append("---")
            all_lines.append("")

    # Write combined file
    combined_content = "\n".join(all_lines)
    combined_path.write_text(combined_content, encoding="utf-8")

    return combined_path
