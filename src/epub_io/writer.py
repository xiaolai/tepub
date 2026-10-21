"""Write a translated EPUB: the source book with some entries replaced.

Content documents and, for translated-only output, the tables of contents are
replaced; every other entry, stylesheets included, is copied unchanged. See
container.py for why this no longer goes through ebooklib.

Translated-only output used to append a rule to the first stylesheet hiding
elements marked as originals. It replaces each original in place, so nothing
carries that marker: the rule did nothing but edit the publisher's CSS.
"""

from __future__ import annotations

import codecs
import re
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from .container import (
    DC_NS,
    MAX_PACKAGE_MEMBER_BYTES,
    OPF_NS,
    Package,
    read_member_bounded,
    read_package,
    resolve,
    secure_xml_parser,
    write_copy,
)
from .titles import title_key

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
    source_language: str | None = None,
    target_language: str | None = None,
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

    with zipfile.ZipFile(input_epub) as archive:
        if toc_updates and css_mode == "translated_only":
            by_entry = {
                package.zip_path(path.as_posix()): titles
                for path, titles in toc_updates.items()
            }
            for toc in _toc_documents(package):
                source = replacements.get(toc) or read_member_bounded(
                    archive, toc, limit=MAX_PACKAGE_MEMBER_BYTES
                )
                replacements[toc] = _retitle(toc, source, by_entry)

        replacements = _declare_languages(
            package,
            replacements,
            archive,
            css_mode=css_mode,
            source_language=source_language,
            target_language=target_language,
        )

    write_copy(input_epub, output_epub, replacements)


def _toc_documents(package: Package) -> list[str]:
    """Every table of contents the book has: an EPUB 3 nav, an NCX, or both."""
    found = []
    for item in (package.nav_item(), package.ncx_item()):
        if item is not None and item.path not in found:
            found.append(item.path)
    return found


def _lookup_title(
    updates: dict[str, dict[str | None, str]],
    entry: str,
    fragment: str | None,
    label: str | None = None,
) -> str | None:
    titles = updates.get(entry)
    if not titles:
        return None
    key = title_key(label)
    if fragment:
        if fragment in titles:
            return titles[fragment]
        if key and key in titles:
            return titles[key]
        # A fragment names one section. Falling back to the document's first
        # heading renamed untranslated subsections after the chapter title.
        return None
    if key and key in titles:
        return titles[key]
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
            title = _lookup_title(
                updates, resolve(toc_path, path), fragment, label.text
            )
            if title:
                label.text = title
    else:
        for nav in root.iter(f"{{{XHTML_NS}}}nav"):
            if "toc" not in (nav.get(f"{{{OPS_NS}}}type") or "").split():
                continue
            for link in nav.iter(f"{{{XHTML_NS}}}a"):
                path, fragment = _split(link.get("href", ""))
                title = _lookup_title(
                    updates,
                    resolve(toc_path, path),
                    fragment,
                    "".join(link.itertext()),
                )
                if title:
                    for child in list(link):
                        link.remove(child)
                    link.text = title
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")


def _declare_languages(
    package: Package,
    replacements: dict[str, bytes],
    archive: zipfile.ZipFile,
    *,
    css_mode: str,
    source_language: str | None,
    target_language: str | None,
) -> dict[str, bytes]:
    """Declare the language(s) the edition is actually written in.

    A translated-only book used to keep the source package's dc:language and
    lang attributes: every reader and screen reader was told Chinese prose was
    English. A bilingual book declared one language although it contains two.
    """
    if not target_language:
        return replacements

    if css_mode == "translated_only":
        codes = [target_language]
    else:
        codes = [code for code in (source_language, target_language) if code]
    codes = list(dict.fromkeys(codes))

    opf = replacements.get(package.opf_path) or read_member_bounded(
        archive, package.opf_path, limit=MAX_PACKAGE_MEMBER_BYTES
    )
    replacements[package.opf_path] = _relabel_package(opf, codes)

    if css_mode == "translated_only":
        for document in package.spine_items():
            if "html" not in document.media_type:
                continue
            # The manifest has the resolved zip path; spine hrefs are decoded,
            # and running them through zip_path would decode again.
            entry = package.manifest[document.idref].path
            content = replacements.get(entry) or read_member_bounded(archive, entry)
            replacements[entry] = _relabel_document(content, target_language)
    return replacements


def _relabel_package(source: bytes, codes: list[str]) -> bytes:
    root = etree.fromstring(source, parser=secure_xml_parser())
    metadata = root.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        metadata = etree.SubElement(root, f"{{{OPF_NS}}}metadata")
    for node in metadata.findall(f"{{{DC_NS}}}language"):
        metadata.remove(node)
    for code in codes:
        language = etree.SubElement(metadata, f"{{{DC_NS}}}language")
        language.text = code
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")


def _document_encoding(source: bytes) -> str:
    """The encoding the document is written in: its byte-order mark, else its
    XML declaration.

    Translated-only output needs each document's original bytes to change only
    in the opening <html> tag; decoding everything as UTF-8 crashed on a UTF-16
    document before the language could be updated.
    """
    if source.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if source.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    match = re.match(rb"\s*<\?xml[^>]*\bencoding=[\"']([^\"']+)[\"']", source)
    if match is not None:
        return match.group(1).decode("ascii")
    return "utf-8"


def _relabel_document(source: bytes, language: str) -> bytes:
    """Set lang and xml:lang on the root element, keeping the DOCTYPE.

    Parsing and re-serialising would drop the document type declaration, which
    XHTML 1.1 content (EPUB 2) still carries.
    """
    encoding = _document_encoding(source)
    text = source.decode(encoding)
    match = re.search(r"<html\b[^>]*>", text)
    if match is None:
        return source
    # Both quote styles: an attribute left behind would now be a duplicate.
    opening = re.sub(
        r"""\s(?:xml:)?lang=(?:"[^"]*"|'[^']*')""", "", match.group(0)
    )
    opening = opening[:-1] + f' lang="{language}" xml:lang="{language}">'
    return (text[: match.start()] + opening + text[match.end() :]).encode(encoding)
