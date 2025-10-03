from __future__ import annotations

from pathlib import Path, PurePosixPath

from ebooklib import ITEM_STYLE, epub

from .resources import get_item_by_href, load_book

TRANSLATED_ONLY_CSS = """
[data-lang=\"original\"] {
  display: none !important;
}

[data-lang=\"translation\"] {
  display: block !important;
}
"""


def write_updated_epub(
    input_epub: Path,
    output_epub: Path,
    updated_html: dict[Path, bytes],
    *,
    toc_updates: dict[PurePosixPath, dict[str | None, str]] | None = None,
    css_mode: str = "bilingual",
) -> None:
    book = load_book(input_epub)
    for href, content in updated_html.items():
        item = get_item_by_href(book, href)
        item.set_content(content)

    if toc_updates and css_mode == "translated_only":
        _rewrite_toc_titles(book, toc_updates)

    if css_mode == "translated_only":
        _append_translated_only_css(book)

    output_epub.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_epub), book)


def _append_translated_only_css(book: epub.EpubBook) -> None:
    for item in book.get_items_of_type(ITEM_STYLE):
        content = item.get_content().decode("utf-8")
        if '[data-lang="original"]' in content:
            return
        content = f"{content}\n\n{TRANSLATED_ONLY_CSS.strip()}\n"
        item.set_content(content.encode("utf-8"))
        return


def _rewrite_toc_titles(
    book: epub.EpubBook, toc_updates: dict[PurePosixPath, dict[str | None, str]]
) -> None:
    def recurse(entries):
        for entry in entries:
            if isinstance(entry, epub.Link):
                path, fragment = _split_href(entry.href)
                title = _lookup_title(toc_updates, path, fragment)
                if title:
                    entry.title = title
            elif isinstance(entry, (list, tuple)):
                recurse(entry)

    recurse(book.toc)


def _split_href(href: str | None) -> tuple[PurePosixPath, str | None]:
    if not href:
        return PurePosixPath(""), None
    if "#" in href:
        path_part, fragment = href.split("#", 1)
    else:
        path_part, fragment = href, None
    return PurePosixPath(path_part), fragment


def _lookup_title(
    toc_updates: dict[PurePosixPath, dict[str | None, str]],
    path: PurePosixPath,
    fragment: str | None,
) -> str | None:
    updates = toc_updates.get(path)
    if not updates:
        return None
    if fragment and fragment in updates:
        return updates[fragment]
    return updates.get(None)
