from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from lxml import etree
from PIL import Image

from epub_io.path_utils import normalize_epub_href
from epub_io.reader import EpubReader
from exceptions import TepubError


class CoverImageError(TepubError):
    """A cover the user chose cannot be read as an image."""


@dataclass
class SpineCoverCandidate:
    href: Path
    document_href: Path


_IMAGE_ATTRS = (
    "src",
    "href",
    "{http://www.w3.org/1999/xlink}href",
)




def find_spine_cover_candidate(reader: EpubReader) -> SpineCoverCandidate | None:
    for document in reader.iter_documents():
        tree = document.tree
        if tree is None:
            continue
        for element in tree.iter():
            try:
                tag_name = etree.QName(element.tag).localname
            except (ValueError, AttributeError):
                continue
            if tag_name not in {"img", "image"}:
                continue
            href_value = None
            for attr in _IMAGE_ATTRS:
                href_value = element.get(attr)
                if href_value:
                    break
            candidate_href_str = normalize_epub_href(document.path, href_value or "")
            if not candidate_href_str:
                continue
            if reader.item_by_href(Path(candidate_href_str)) is None:
                continue
            return SpineCoverCandidate(href=Path(candidate_href_str), document_href=document.path)
    return None


def _find_cover_item(reader: EpubReader):
    """The package's declared cover, else a spine cover image, else a likely image."""
    declared = reader.item_for(reader.package.cover_item())
    if declared is not None and declared.is_image:
        return declared
    spine_candidate = find_spine_cover_candidate(reader)
    if spine_candidate:
        item = reader.item_by_href(spine_candidate.href)
        if item is not None:
            return item
    images = [item for item in reader.items() if item.is_image]
    for item in images:
        if "cover" in item.href.as_posix().lower():
            return item
    return images[0] if images else None

def _prepare_cover(
    output_root: Path,
    reader: EpubReader,
    explicit_cover: Path | None = None,
) -> Path | None:
    if explicit_cover:
        try:
            image = Image.open(explicit_cover)
        except OSError as exc:
            # Returning None here dropped a cover the user asked for and wrote
            # the book without one.
            raise CoverImageError(
                f"Cover {explicit_cover} cannot be read as an image: {exc}"
            ) from exc
        # Preserve original format if PNG
        original_format = image.format  # 'PNG', 'JPEG', etc.
    else:
        try:
            cover_item = _find_cover_item(reader)
            if not cover_item:
                return None
            image = Image.open(BytesIO(reader.read_bytes(cover_item)))
            original_format = image.format
        except Exception:
            # Detected art is a guess; a book whose images PIL cannot read
            # simply has no cover.
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
