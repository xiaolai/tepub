from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader

console = get_console()


@dataclass
class ImageInfo:
    """Information about an extracted image."""

    epub_path: Path  # Original path in EPUB
    extracted_path: Path  # Path where image was extracted
    is_cover_candidate: bool = False


# Supported image extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}


def _is_image_item(item) -> bool:
    """Check if an EPUB item is an image."""
    if hasattr(item, "media_type"):
        return item.media_type.startswith("image/")
    if hasattr(item, "file_name"):
        ext = Path(item.file_name).suffix.lower()
        return ext in IMAGE_EXTENSIONS
    return False


def _is_potential_cover(file_path: Path, is_first_manifest_image: bool) -> bool:
    """Determine if an image is likely a cover candidate.

    Note: the positional heuristic is based on manifest order, not spine order —
    the spine is never inspected here. Manifest order usually puts the cover
    first, but it is a weaker signal than the filename patterns above it.
    """
    name_lower = file_path.name.lower()

    # Check filename patterns
    if "cover" in name_lower:
        return True
    if "title" in name_lower:
        return True

    # The first image in manifest order is often the cover
    if is_first_manifest_image:
        return True

    return False


def extract_images(
    settings: AppSettings,
    input_epub: Path,
    output_dir: Path,
) -> list[ImageInfo]:
    """
    Extract all images from EPUB to output directory.

    Args:
        settings: Application settings
        input_epub: Path to source EPUB
        output_dir: Directory where images will be saved (typically {markdown_dir}/images)

    Returns:
        List of ImageInfo objects with extraction details
    """
    reader = EpubReader(input_epub, settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted_images: list[ImageInfo] = []
    seen_first_manifest_image = False
    # Names claimed during *this* run. Previously the loop tested output_path.exists(),
    # so a rerun saw every prior output as a duplicate and emitted image_1.jpg,
    # image_2.jpg, … growing without bound and leaving stale files behind.
    used_names: set[str] = set()

    # Extract all image items from EPUB
    for item in reader.book.get_items():
        if not _is_image_item(item):
            continue

        epub_path = Path(item.file_name)

        # Generate output filename (preserve original name, handle duplicates)
        output_filename = epub_path.name

        # Disambiguate only against names already written in this run, so output is
        # deterministic across runs and managed files are overwritten in place.
        if output_filename in used_names:
            counter = 1
            while output_filename in used_names:
                counter += 1
                output_filename = f"{epub_path.stem}_{counter}{epub_path.suffix}"
        used_names.add(output_filename)
        output_path = output_dir / output_filename

        # Read the item. A single unreadable manifest entry is recoverable —
        # skip it and carry on.
        try:
            content = item.get_content()
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to read image {epub_path}: {e}[/yellow]")
            used_names.discard(output_filename)
            continue

        # Write failures are NOT recoverable: swallowing them produced an export
        # that reported success while silently missing images.
        output_path.write_bytes(content)

        # Check if this could be a cover
        is_cover_candidate = _is_potential_cover(epub_path, not seen_first_manifest_image)
        seen_first_manifest_image = True

        extracted_images.append(
            ImageInfo(
                epub_path=epub_path,
                extracted_path=output_path,
                is_cover_candidate=is_cover_candidate,
            )
        )

    return extracted_images


def get_image_mapping(extracted_images: list[ImageInfo]) -> dict[str, str]:
    """
    Create mapping from EPUB image paths to extracted filenames.

    Args:
        extracted_images: List of ImageInfo objects

    Returns:
        Dictionary mapping EPUB path (as posix string) to extracted filename
    """
    mapping = {}
    for img_info in extracted_images:
        epub_key = img_info.epub_path.as_posix()
        extracted_name = img_info.extracted_path.name
        mapping[epub_key] = extracted_name

    return mapping
