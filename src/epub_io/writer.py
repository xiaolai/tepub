"""Write a translated EPUB: the source book with some entries replaced.

Content documents and, for translated-only output, the tables of contents are
replaced; every other entry, stylesheets included, is copied unchanged. See
container.py for why this no longer goes through ebooklib.

Translated-only output used to append a rule to the first stylesheet hiding
elements marked as originals. It replaces each original in place, so nothing
carries that marker: the rule did nothing but edit the publisher's CSS.
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from .container import Package, read_package, resolve, secure_xml_parser, write_copy

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

TitleUpdates = dict[PurePosixPath, dict[str | None, str]]


def write_updated_epub(
    input_epub: Path,
    output_epub: Path,
    updated_html: dict[Path, bytes],
    *,
    toc_updates: TitleUpdates | None = None,
    css_mode: str = "bilingual",
) -> None:
    """Write ``input_epub`` to ``output_epub`` with ``updated_html`` applied.

    ``updated_html`` and ``toc_updates`` are keyed by package-relative paths, as
    the manifest writes them.
    """
    package = read_package(input_epub)
    replacements = {
        package.zip_path(PurePosixPath(href).as_posix()): content
        for href, content in updated_html.items()
    }

    if toc_updates and css_mode == "translated_only":
        by_entry = {
            package.zip_path(path.as_posix()): titles for path, titles in toc_updates.items()
        }
        with zipfile.ZipFile(input_epub) as archive:
            for toc in _toc_documents(package):
                source = replacements.get(toc) or archive.read(toc)
                replacements[toc] = _retitle(toc, source, by_entry)

    write_copy(input_epub, output_epub, replacements)


def _toc_documents(package: Package) -> list[str]:
    """Every table of contents the book has: an EPUB 3 nav, an NCX, or both."""
    found = []
    for item in (package.nav_item(), package.ncx_item()):
        if item is not None and item.path not in found:
            found.append(item.path)
    return found


def _lookup_title(
    updates: dict[str, dict[str | None, str]], entry: str, fragment: str | None
) -> str | None:
    titles = updates.get(entry)
    if not titles:
        return None
    if fragment and fragment in titles:
        return titles[fragment]
    return titles.get(None)


def _split(href: str) -> tuple[str, str | None]:
    path, _, fragment = href.partition("#")
    return path, fragment or None


def _retitle(toc_path: str, source: bytes, updates: dict[str, dict[str | None, str]]) -> bytes:
    root = etree.fromstring(source, parser=secure_xml_parser())
    if root.tag == f"{{{NCX_NS}}}ncx":
        for point in root.iter(f"{{{NCX_NS}}}navPoint"):
            content = point.find(f"{{{NCX_NS}}}content")
            label = point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
            if content is None or label is None:
                continue
            path, fragment = _split(content.get("src", ""))
            title = _lookup_title(updates, resolve(toc_path, path), fragment)
            if title:
                label.text = title
    else:
        for nav in root.iter(f"{{{XHTML_NS}}}nav"):
            if "toc" not in (nav.get(f"{{{OPS_NS}}}type") or "").split():
                continue
            for link in nav.iter(f"{{{XHTML_NS}}}a"):
                path, fragment = _split(link.get("href", ""))
                title = _lookup_title(updates, resolve(toc_path, path), fragment)
                if title:
                    for child in list(link):
                        link.remove(child)
                    link.text = title
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")
