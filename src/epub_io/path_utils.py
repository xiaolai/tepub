"""Utilities for normalizing EPUB paths and hrefs."""

from __future__ import annotations

import posixpath
from pathlib import Path, PurePosixPath


def normalize_epub_href(document_path: Path, raw_href: str) -> str | None:
    """Normalize EPUB href relative to document path.

    Resolves relative hrefs against the document's directory and normalizes
    the result to a clean POSIX path. Filters out invalid hrefs like data URIs,
    external URLs, and paths that traverse outside the EPUB root.

    Args:
        document_path: Path to the document containing the href
        raw_href: The href attribute value to normalize

    Returns:
        Normalized POSIX path string, or None if the href is invalid.

    Examples:
        >>> normalize_epub_href(Path("text/chapter1.xhtml"), "image.jpg")
        "text/image.jpg"

        >>> normalize_epub_href(Path("text/chapter1.xhtml"), "../images/cover.jpg")
        "images/cover.jpg"

        >>> normalize_epub_href(Path("text/chapter1.xhtml"), "data:image/png;base64,...")
        None

        >>> normalize_epub_href(Path("text/chapter1.xhtml"), "http://example.com/img.jpg")
        None
    """
    # Validate input
    if not raw_href:
        return None

    value = raw_href.strip()
    if not value:
        return None

    # Filter out data URIs and external URLs
    if value.startswith("data:"):
        return None
    if "://" in value:
        return None

    # Convert document path to POSIX for consistent handling
    doc_posix = PurePosixPath(document_path.as_posix())

    # Resolve href relative to document's directory
    if value.startswith("/"):
        # Absolute path within EPUB (relative to EPUB root)
        candidate = PurePosixPath(value.lstrip("/"))
    else:
        # Relative path (relative to document's directory)
        doc_dir = doc_posix.parent
        candidate = doc_dir.joinpath(PurePosixPath(value))

    # Normalize the path (resolve .. and .)
    normalized = PurePosixPath(posixpath.normpath(str(candidate)))

    # Reject paths that traverse outside EPUB root
    if normalized.as_posix().startswith("../"):
        return None

    return normalized.as_posix()
