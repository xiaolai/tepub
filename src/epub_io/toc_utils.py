"""Utilities for parsing EPUB table of contents (TOC)."""

from __future__ import annotations

from epub_io.container import iter_toc
from epub_io.reader import EpubReader


def parse_toc_to_dict(reader: EpubReader) -> dict[str, str]:
    """Extract TOC titles mapped by document href.

    Parses the EPUB's table of contents and creates a mapping from
    document hrefs (without fragments) to their titles.

    Args:
        reader: EpubReader instance with loaded EPUB

    Returns:
        Dictionary mapping href (without fragment) to title.
        For example: {"chapter1.xhtml": "Chapter 1: Introduction"}

    Examples:
        >>> reader = EpubReader(epub_path, settings)
        >>> toc_map = parse_toc_to_dict(reader)
        >>> toc_map.get("intro.xhtml")
        "Introduction"
    """
    mapping: dict[str, str] = {}

    def _record(node) -> None:
        href = (node.href or "").split("#", 1)[0]  # Remove fragment
        if not href:
            # Structural sections carry no target; keying them under "" put a
            # bogus entry in the mapping that later lookups could match.
            return
        title = node.title or ""
        if not title:
            # An entry with no title still registers its document, matching the
            # previous behaviour, but must not clear a title already recorded.
            mapping.setdefault(href, "")
            return
        # Several fragment-level entries can point at one document. Plain
        # assignment let the last one win, so a document's title became whichever
        # sub-section appeared last. The first entry introduces the document.
        if not mapping.get(href):
            mapping[href] = title

    for entry in iter_toc(reader.package.toc):
        _record(entry)
    return mapping
