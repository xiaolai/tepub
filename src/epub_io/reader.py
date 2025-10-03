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

    def iter_documents(self) -> Iterable[HtmlDocument]:
        for spine_item in iter_spine_items(self.book):
            if not spine_item.media_type.startswith("application/xhtml"):
                continue
            item = get_item_by_href(self.book, spine_item.href)
            raw_html: bytes = item.get_content()
            tree = html.fromstring(raw_html)
            yield HtmlDocument(spine_item=spine_item, tree=tree, raw_html=raw_html)

    def get_document(self, href: Path) -> HtmlDocument:
        item = get_item_by_href(self.book, href)
        raw_html: bytes = item.get_content()
        tree = html.fromstring(raw_html)
        spine_item = next(
            (sp for sp in iter_spine_items(self.book) if sp.href == href),
            None,
        )
        if spine_item is None:
            raise KeyError(f"Spine item not found for {href}")
        return HtmlDocument(spine_item=spine_item, tree=tree, raw_html=raw_html)
