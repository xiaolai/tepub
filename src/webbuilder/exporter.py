from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from ebooklib import ITEM_DOCUMENT, epub
from lxml import html as lxml_html

from config import AppSettings
from epub_io.path_utils import safe_relative_member
from epub_io.reader import EpubReader
from epub_io.resources import iter_spine_items
from injection.engine import apply_translations

from .assets import BookData, copy_static_assets, render_index
from .dom import clean_html, ensure_parseable


def _default_output_dir(epub_path: Path, work_dir: Path) -> Path:
    # Export to workspace directory, not alongside EPUB
    return work_dir / f"{epub_path.stem}_web"


def _book_title(reader: EpubReader) -> str:
    metadata = reader.book.get_metadata("DC", "title")
    if metadata:
        return metadata[0][0]
    return reader.epub_path.stem


def _document_title(tree) -> str:
    candidates = tree.xpath("//h1")
    if candidates:
        text = candidates[0].text_content().strip()
        if text:
            return text
    titles = tree.xpath("//title")
    if titles:
        text = titles[0].text_content().strip()
        if text:
            return text
    return ""


def _build_spine(reader: EpubReader, doc_titles: dict[Path, str]) -> list[dict]:
    spine: list[dict] = []
    for item in iter_spine_items(reader.book):
        title = doc_titles.get(item.href, item.href.stem)
        spine.append(
            {
                "href": item.href.as_posix(),
                "title": title or item.href.stem,
            }
        )
    return spine


def _parse_toc(entries) -> list[dict]:
    toc_list: list[dict] = []

    def recurse(items, level=0):
        for item in items:
            if isinstance(item, epub.Link):
                toc_list.append(
                    {
                        "title": item.title,
                        "href": item.href,
                        "level": level,
                    }
                )
            elif isinstance(item, (list, tuple)) and item:
                head = item[0]
                children = item[1] if len(item) > 1 else []
                if isinstance(head, epub.Link):
                    toc_list.append(
                        {
                            "title": head.title,
                            "href": head.href,
                            "level": level,
                        }
                    )
                recurse(children, level + 1)

    recurse(entries)
    return toc_list


def _copy_static_resources(reader: EpubReader, content_dir: Path) -> None:
    for item in reader.book.get_items():
        # Skip HTML documents; they are handled separately
        if item.get_type() == ITEM_DOCUMENT:
            continue
        # Manifest names are book-controlled. Joining them unchecked let a
        # crafted EPUB write outside content_dir — pathlib discards the base
        # entirely for an absolute name, and ".." walks upward.
        member = safe_relative_member(item.file_name, reader.epub_path)
        dest = content_dir / Path(*member.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(item.get_content())


def export_web(
    settings: AppSettings,
    input_epub: Path,
    *,
    output_dir: Path | None = None,
    output_mode: str | None = None,
) -> Path:
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
    updated_html, title_updates = apply_translations(settings, input_epub, mode=mode)

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
    toc = _parse_toc(reader.book.toc) if reader.book.toc else []
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

    return output_root
