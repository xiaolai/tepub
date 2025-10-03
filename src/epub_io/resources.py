from __future__ import annotations

import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ebooklib import epub


@dataclass
class SpineItem:
    index: int
    idref: str
    href: Path
    media_type: str
    linear: bool


def load_book(epub_path: Path) -> epub.EpubBook:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="In the future version we will turn default option ignore_ncx to True.",
        )
        warnings.filterwarnings(
            "ignore",
            message="This search incorrectly ignores the root element",
        )
        return epub.read_epub(str(epub_path), options={"ignore_ncx": False})


def iter_spine_items(book: epub.EpubBook) -> Iterable[SpineItem]:
    manifest = {item.id: item for item in book.get_items()}
    for idx, (idref, linear) in enumerate(book.spine):
        item = manifest.get(idref)
        if item is None:
            continue
        href = Path(item.file_name)
        yield SpineItem(
            index=idx,
            idref=idref,
            href=href,
            media_type=item.media_type,
            linear=linear == "yes" if isinstance(linear, str) else bool(linear),
        )


def get_html_items(book: epub.EpubBook) -> list[epub.EpubHtml]:
    return [item for item in book.get_items() if item.get_type() == epub.ITEM_DOCUMENT]


def get_item_by_href(book: epub.EpubBook, href: Path):
    # ebooklib stores file_name using forward slashes
    target = href.as_posix()
    for item in book.get_items():
        if item.file_name == target:
            return item
    raise KeyError(f"No item found for href {href}")


def extract_metadata(book: epub.EpubBook) -> dict[str, str | None]:
    """Extract book metadata from EPUB.

    Returns:
        Dictionary with keys: title, author, publisher, year
        Values are None if metadata is not available
    """
    DC = "http://purl.org/dc/elements/1.1/"

    # Extract title
    title_meta = book.get_metadata(DC, "title")
    title = title_meta[0][0] if title_meta else None

    # Extract creator/author
    creator_meta = book.get_metadata(DC, "creator")
    author = creator_meta[0][0] if creator_meta else None

    # Extract publisher
    publisher_meta = book.get_metadata(DC, "publisher")
    publisher = publisher_meta[0][0] if publisher_meta else None

    # Extract date (try to extract just the year)
    date_meta = book.get_metadata(DC, "date")
    year = None
    if date_meta:
        date_str = date_meta[0][0]
        # Try to extract year from various date formats (YYYY, YYYY-MM-DD, etc.)
        if date_str:
            year = date_str[:4] if len(date_str) >= 4 and date_str[:4].isdigit() else date_str

    return {
        "title": title,
        "author": author,
        "publisher": publisher,
        "year": year,
    }
