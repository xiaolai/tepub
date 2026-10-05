from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lxml import html

from config import AppSettings

from .resources import SpineItem, get_item_by_href, iter_spine_items, load_book

# Maximum EPUB file size: 500MB
MAX_EPUB_SIZE = 500 * 1024 * 1024


@dataclass
class HtmlDocument:
    spine_item: SpineItem
    tree: html.HtmlElement
    raw_html: bytes

    @property
    def path(self) -> Path:
        return self.spine_item.href


class EpubReader:
    def __init__(self, epub_path: Path, settings: AppSettings):
        self.epub_path = epub_path
        self.settings = settings

        # Validate file size before processing
        if not epub_path.exists():
            raise FileNotFoundError(f"EPUB file not found: {epub_path}")

        file_size = epub_path.stat().st_size
        if file_size > MAX_EPUB_SIZE:
            size_mb = file_size / (1024 * 1024)
            max_mb = MAX_EPUB_SIZE / (1024 * 1024)
            raise ValueError(
                f"EPUB file too large: {size_mb:.1f}MB (maximum: {max_mb:.0f}MB)"
            )

        self.book = load_book(epub_path)
        self._documents: dict[Path, HtmlDocument] | None = None

    def iter_documents(self) -> Iterable[HtmlDocument]:
        for spine_item in iter_spine_items(self.book):
            if not spine_item.media_type.startswith("application/xhtml"):
                continue
            item = get_item_by_href(self.book, spine_item.href)
            raw_html: bytes = item.get_content()
            tree = html.fromstring(raw_html)
            yield HtmlDocument(spine_item=spine_item, tree=tree, raw_html=raw_html)

    def read_document_by_path(self, href: Path) -> HtmlDocument:
        """Return the parsed spine document at ``href``, parsing each one once.

        Audiobook preprocessing looks a document up for every segment it holds,
        so documents are parsed on first use and kept. Callers must not modify
        the returned tree; clone it first.

        Raises KeyError when ``href`` is not a document in this book's spine,
        which means the segments were extracted from a different EPUB.
        """
        if self._documents is None:
            self._documents = {document.path: document for document in self.iter_documents()}
        try:
            return self._documents[Path(href)]
        except KeyError:
            raise KeyError(
                f"{href} is not a content document in {self.epub_path.name}; "
                "the segments may come from a different EPUB"
            ) from None
