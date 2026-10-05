from __future__ import annotations

import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from config import AppSettings
from logging_utils.logger import get_logger

from .container import read_package
from .resources import SpineItem, iter_spine_items, load_book
from .xhtml import NotWellFormed, XhtmlDocument, parse_xhtml

logger = get_logger(__name__)

# Maximum EPUB file size: 500MB
MAX_EPUB_SIZE = 500 * 1024 * 1024


@dataclass
class HtmlDocument:
    """One spine document, parsed as XML.

    ``tree`` and ``xhtml`` are None when the document is not well-formed XML; it
    is then left untranslated and copied into the output unchanged.
    """

    spine_item: SpineItem
    tree: etree._Element | None
    raw_html: bytes
    xhtml: XhtmlDocument | None = None

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
        self.package = read_package(epub_path)
        self._documents: dict[Path, HtmlDocument] | None = None
        # Per-document results that consumers compute once and reuse, such as
        # located translation units.
        self.unit_cache: dict[Path, object] = {}

    def iter_documents(self) -> Iterable[HtmlDocument]:
        # One open for the whole walk: reopening per document re-reads the zip's
        # directory every time, which is slow on books with thousands of entries.
        with zipfile.ZipFile(self.epub_path) as archive:
            for spine_item in iter_spine_items(self.book):
                if not spine_item.media_type.startswith("application/xhtml"):
                    continue
                # The file's own bytes. ebooklib's get_content() returns a rebuilt
                # document with an empty <head>, so every translated chapter used
                # to lose its title and stylesheet links.
                raw_html = archive.read(self.package.zip_path(spine_item.href.as_posix()))
                try:
                    xhtml = parse_xhtml(raw_html)
                except NotWellFormed as exc:
                    logger.warning(
                        "%s is %s; it will be left untranslated", spine_item.href.as_posix(), exc
                    )
                    yield HtmlDocument(spine_item=spine_item, tree=None, raw_html=raw_html)
                    continue
                yield HtmlDocument(
                    spine_item=spine_item, tree=xhtml.root, raw_html=raw_html, xhtml=xhtml
                )

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
