from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from lxml import etree
from lxml import html as lxml_html

from config import AppSettings
from epub_io.container import TocEntry, read_member_bounded, secure_xml_parser
from epub_io.path_utils import safe_relative_member
from epub_io.reader import EpubReader
from epub_io.titles import title_key
from epub_io.xhtml import document_title
from injection.engine import Injection, apply_translations

from .assets import BookData, copy_static_assets, render_index
from .dom import clean_html, ensure_parseable

_ACTIVE_MARKUP = frozenset({"text/html", "application/xhtml+xml", "image/svg+xml"})
_UNSAFE_SVG = frozenset(
    {
        "script", "foreignobject", "animate", "animatemotion", "animatetransform",
        "set", "handler", "iframe", "object", "embed",
    }
)
_URL_ATTRIBUTES = frozenset({"href", "src", "xlink:href"})
_UNSAFE_SCHEMES = ("javascript:", "vbscript:", "data:text/html", "data:application/xhtml")


def _sanitise_svg(data: bytes) -> bytes:
    root = etree.fromstring(data, parser=secure_xml_parser())
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        if etree.QName(node.tag).localname.lower() in _UNSAFE_SVG:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
            continue
        for name in list(node.attrib):
            local = etree.QName(name).localname.lower() if name.startswith("{") else name.lower()
            value = node.attrib[name]
            if local.startswith("on") or (
                local in _URL_ATTRIBUTES
                and value.replace(" ", "").lower().startswith(_UNSAFE_SCHEMES)
            ):
                del node.attrib[name]
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def _sanitised_resource(item, data: bytes) -> bytes:
    """Active markup copied into the site is cleaned; an <img> does not run it,
    but a book link can, and the viewer used to fetch any target."""
    if item.media_type == "image/svg+xml":
        try:
            return _sanitise_svg(data)
        except etree.XMLSyntaxError:
            return b""  # unparseable active markup is not served
    if item.media_type in {"text/html", "application/xhtml+xml"}:
        return clean_html(data).encode("utf-8")
    return data


def _default_output_dir(epub_path: Path, work_dir: Path) -> Path:
    # Export to workspace directory, not alongside EPUB
    return work_dir / f"{epub_path.stem}_web"


def _book_title(reader: EpubReader) -> str:
    return reader.package.metadata.title or reader.epub_path.stem


def _document_title(tree) -> str:
    # Works on the reader's XHTML trees and on lxml.html trees of cleaned
    # content alike; //h1 found nothing in a namespaced tree.
    if tree is None:
        return ""
    return document_title(tree)


def _build_spine(reader: EpubReader, doc_titles: dict[Path, str]) -> list[dict]:
    spine: list[dict] = []
    for item in reader.package.spine_items():
        title = doc_titles.get(item.href, item.href.stem)
        spine.append(
            {
                # Archive-root-relative, the layout documents are written in,
                # so a manifest path may legitimately begin with "../".
                "href": reader.package.zip_path(item.href.as_posix()),
                "title": title or item.href.stem,
            }
        )
    return spine


def _parse_toc(entries: list[TocEntry]) -> list[dict]:
    toc_list: list[dict] = []

    def recurse(items: list[TocEntry], level: int = 0) -> None:
        for entry in items:
            if entry.href:
                toc_list.append({"title": entry.title, "href": entry.href, "level": level})
            recurse(entry.children, level + 1)

    recurse(entries)
    return toc_list


def _copy_static_resources(
    reader: EpubReader,
    content_dir: Path,
    documents: dict[str, str],
    exported: set[str],
) -> None:
    # One open for the whole walk: read_bytes reopened the archive per resource,
    # and a resource-heavy book made that quadratic in the zip's entry count.
    with zipfile.ZipFile(reader.epub_path) as archive:
        for item in reader.items():
            # Spine documents were written by the document loop, translated and
            # cleaned; copying their raw bytes would undo that.
            if item.path in exported:
                continue
            # Manifest names are book-controlled. Joining them unchecked let a
            # crafted EPUB write outside content_dir — pathlib discards the base
            # entirely for an absolute name, and ".." walks upward.
            member = safe_relative_member(item.path, reader.epub_path)
            dest = content_dir / Path(*member.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = read_member_bounded(archive, item.path)
            if item.is_document:
                # A linked document outside the spine (footnotes, endnotes) is
                # not translated, but it is rendered, so it needs the same
                # cleaning and "content/" URL prefix as a spine chapter. It was
                # skipped entirely, so its links pointed at nothing.
                content = clean_html(data, relative_path=Path(item.path))
                documents[item.path] = content
                dest.write_text(content, encoding="utf-8")
            else:
                dest.write_bytes(_sanitised_resource(item, data))


@dataclass(frozen=True)
class WebExport:
    """The site written, and the units that could not be inserted into it."""

    site: Path
    failed: tuple[str, ...] = ()


def export_web(
    settings: AppSettings,
    input_epub: Path,
    *,
    output_dir: Path | None = None,
    output_mode: str | None = None,
    injection: Injection | None = None,
) -> WebExport:
    """``injection``: apply_translations' result for the same mode, when the
    caller already has it, as export has when it also writes the EPUB."""
    output_root = (
        Path(output_dir) if output_dir else _default_output_dir(input_epub, settings.work_dir)
    )

    mode_value = output_mode or getattr(settings, "output_mode", "bilingual")
    mode = mode_value.replace("-", "_").lower() if isinstance(mode_value, str) else "bilingual"
    if mode not in {"bilingual", "translated_only"}:
        mode = "bilingual"

    # Read the EPUB and apply translations *before* touching the existing export.
    # The previous output used to be deleted first, so an unreadable EPUB or a
    # failure during translation left the user with no export at all.
    reader = EpubReader(input_epub, settings)
    # mode was not forwarded, so an explicit output_mode differing from the
    # configured one was ignored for the injection step.
    if injection is None:
        injection = apply_translations(settings, input_epub, mode=mode)
    updated_html, title_updates = injection.updated_html, injection.title_updates

    # Build into a staging directory and swap it in only once the site is
    # complete. Deleting the previous export up front left a partial one behind
    # whenever a later document or resource failed.
    staging = output_root.with_name(output_root.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        copy_static_assets(staging)

        doc_titles: dict[Path, str] = {}
        documents: dict[str, str] = {}
        exported: set[str] = set()
        content_dir = staging / "content"
        for document in reader.iter_documents():
            path = document.path
            # Documents are written at their archive path: a manifest href may
            # be package-relative ("../Text/ch.xhtml"), which is legitimate
            # inside the book but cannot be a destination below content_dir.
            archive_path = reader.package.zip_path(path.as_posix())
            exported.add(archive_path)
            if path in updated_html:
                content = clean_html(updated_html[path], relative_path=Path(archive_path))
            else:
                content = clean_html(document.raw_html, relative_path=Path(archive_path))
            ensure_parseable(content)
            if mode == "translated_only":
                # lxml_html is imported unconditionally, so the old `in globals()`
                # guard was always true and its else branch unreachable. Titles come
                # from the *cleaned* content here so they reflect the translation.
                doc_titles[path] = _document_title(lxml_html.fromstring(content)) or path.stem
            else:
                doc_titles[path] = _document_title(document.tree) or path.stem
            documents[archive_path] = content
            # Documents come from the same untrusted manifest as static resources and
            # need the same guard; only the static-resource path was validated, so a
            # document named "../escape.xhtml" still wrote outside content_dir.
            doc_member = safe_relative_member(archive_path, reader.epub_path)
            dest = content_dir / Path(*doc_member.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        _copy_static_resources(reader, content_dir, documents, exported)

        spine = _build_spine(reader, doc_titles)
        toc = _parse_toc(reader.package.toc)
        # title_updates was computed and then discarded, so translated-only exports
        # kept the original TOC labels beside translated content.
        if title_updates:
            for entry in toc:
                href = str(entry.get("href", ""))
                path_part, _, fragment = href.partition("#")
                updates = title_updates.get(PurePosixPath(path_part))
                if not updates:
                    continue
                translated = updates.get(fragment or None)
                if not translated:
                    # The fragment often names a section wrapper rather than
                    # the heading; the heading's text is the recorded link.
                    key = title_key(entry["title"])
                    translated = updates.get(key) if key else None
                if not translated:
                    translated = updates.get(None)
                if translated:
                    entry["title"] = translated
        # Navigation targets use the archive layout, like the documents and the
        # links rewritten inside them.
        for entry in toc:
            href = str(entry.get("href", ""))
            path_part, _, fragment = href.partition("#")
            if not path_part:
                continue
            # TOC hrefs are raw; zip_path expects an already-decoded path.
            archive_path = reader.package.zip_path(unquote(path_part))
            entry["href"] = f"{archive_path}#{fragment}" if fragment else archive_path
        if not toc:
            toc = [{"title": entry["title"], "href": entry["href"], "level": 0} for entry in spine]

        render_index(
            staging,
            BookData(
                title=_book_title(reader),
                spine=spine,
                toc=toc,
                documents=documents,
            ),
        )
        if output_root.exists():
            shutil.rmtree(output_root)
        staging.rename(output_root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return WebExport(output_root, injection.failed)
