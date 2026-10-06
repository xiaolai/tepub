from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import html as lxml_html

from config import AppSettings
from epub_io.container import TocEntry
from epub_io.path_utils import safe_relative_member
from epub_io.reader import EpubReader
from epub_io.xhtml import document_title
from injection.engine import Injection, apply_translations

from .assets import BookData, copy_static_assets, render_index
from .dom import clean_html, ensure_parseable


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
                "href": item.href.as_posix(),
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


def _copy_static_resources(reader: EpubReader, content_dir: Path) -> None:
    for item in reader.items():
        # Skip HTML documents; they are handled separately
        if item.is_document:
            continue
        # Manifest names are book-controlled. Joining them unchecked let a
        # crafted EPUB write outside content_dir — pathlib discards the base
        # entirely for an absolute name, and ".." walks upward.
        member = safe_relative_member(item.href.as_posix(), reader.epub_path)
        dest = content_dir / Path(*member.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(reader.read_bytes(item))


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

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    copy_static_assets(output_root)

    doc_titles: dict[Path, str] = {}
    documents: dict[str, str] = {}
    content_dir = output_root / "content"
    for document in reader.iter_documents():
        path = document.path
        if path in updated_html:
            content = clean_html(updated_html[path], relative_path=path)
        else:
            content = clean_html(document.raw_html, relative_path=path)
        ensure_parseable(content)
        if mode == "translated_only":
            # lxml_html is imported unconditionally, so the old `in globals()`
            # guard was always true and its else branch unreachable. Titles come
            # from the *cleaned* content here so they reflect the translation.
            doc_titles[path] = _document_title(lxml_html.fromstring(content)) or path.stem
        else:
            doc_titles[path] = _document_title(document.tree) or path.stem
        documents[path.as_posix()] = content
        # Documents come from the same untrusted manifest as static resources and
        # need the same guard; only the static-resource path was validated, so a
        # document named "../escape.xhtml" still wrote outside content_dir.
        doc_member = safe_relative_member(path.as_posix(), reader.epub_path)
        dest = content_dir / Path(*doc_member.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    _copy_static_resources(reader, content_dir)

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
            translated = updates.get(fragment or None) or updates.get(None)
            if translated:
                entry["title"] = translated
    if not toc:
        toc = [{"title": entry["title"], "href": entry["href"], "level": 0} for entry in spine]

    render_index(
        output_root,
        BookData(
            title=_book_title(reader),
            spine=spine,
            toc=toc,
            documents=documents,
        ),
    )

    return WebExport(output_root, injection.failed)
