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


def _is_potential_cover(file_path: Path, is_first_spine_image: bool) -> bool:
    """Determine if an image is likely a cover candidate."""
    name_lower = file_path.name.lower()

    # Check filename patterns
    if "cover" in name_lower:
        return True
    if "title" in name_lower:
        return True

    # First image in spine is often the cover
    if is_first_spine_image:
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
    seen_first_spine_image = False

    # Extract all image items from EPUB
    for item in reader.book.get_items():
        if not _is_image_item(item):
            continue

        epub_path = Path(item.file_name)

        # Generate output filename (preserve original name, handle duplicates)
        output_filename = epub_path.name
        output_path = output_dir / output_filename

        # Handle duplicate filenames by adding a counter
        counter = 1
        while output_path.exists():
            stem = epub_path.stem
            suffix = epub_path.suffix
            output_filename = f"{stem}_{counter}{suffix}"
            output_path = output_dir / output_filename
            counter += 1

        # Write image file
        try:
            content = item.get_content()
            output_path.write_bytes(content)

            # Check if this could be a cover
            is_cover_candidate = _is_potential_cover(epub_path, not seen_first_spine_image)
            if not seen_first_spine_image:
                seen_first_spine_image = True

            extracted_images.append(
                ImageInfo(
                    epub_path=epub_path,
                    extracted_path=output_path,
                    is_cover_candidate=is_cover_candidate,
                )
            )
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to extract image {epub_path}: {e}[/yellow]")
            continue

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
