"""Utilities for parsing EPUB table of contents (TOC)."""

from __future__ import annotations

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

    def recurse(entries):
        """Recursively traverse TOC entries."""
        for item in entries:
            # Handle direct Link objects
            if hasattr(item, "href") and hasattr(item, "title"):
                href = item.href.split("#", 1)[0]  # Remove fragment
                mapping[href] = item.title or mapping.get(href, "")
            # Handle nested tuple/list structure (older EpubPy format)
            elif isinstance(item, (list, tuple)) and item:
                head = item[0]
                if hasattr(head, "href") and hasattr(head, "title"):
                    href = head.href.split("#", 1)[0]
                    mapping[href] = head.title or mapping.get(href, "")
                # Recurse into children if they exist
                if len(item) > 1:
                    recurse(item[1])

    toc = reader.book.toc or []
    recurse(toc)
    return mapping
