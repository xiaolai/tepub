"""Every piece of body text is in exactly one unit, skipped on purpose, or reported.

The old rules extracted some text twice and, once a leaf-block rule was proposed,
would have lost text that sits beside block children. This holds the rule to
its promise over the fixtures and, opt-in, over a folder of real books.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from epub_io.container import read_package
from epub_io.xhtml import local_name, parse_xhtml
from extraction.segments import (
    SKIPPED_TAGS,
    _has_block_descendant,
    _is_block,
    body_of,
    iter_units,
    loose_body_text,
)
from tests.epub_fixtures import FIXTURES


def _misplaced_text(root) -> list[tuple[int, str]]:
    """(number of units containing it, text) for text not in exactly one unit."""
    body = body_of(root)
    if body is None:
        return []
    units = [element for element, _mode in iter_units(body)]  # kept alive for `is`

    def units_around(element) -> int | None:
        count = 0
        node = element
        while node is not None and node is not body:
            if local_name(node) in SKIPPED_TAGS:
                return None
            count += any(node is unit for unit in units)
            node = node.getparent()
        return count

    def is_loose(element) -> bool:
        """Text whose body-level ancestor is inline, as loose_body_text counts it."""
        if element is body:
            return True
        node = element
        while node.getparent() is not body:
            node = node.getparent()
        return not _is_block(node) and not _has_block_descendant(node)

    found = []
    for element in body.iter():
        if not isinstance(element.tag, str):
            continue
        for text, owner in ((element.text, element), (element.tail, element.getparent())):
            if owner is None or not (text and text.strip()):
                continue
            if element is body and owner is not body:
                continue
            count = units_around(owner)
            if count is None or count == 1:
                continue
            if count == 0 and is_loose(owner):
                continue  # reported by loose_body_text
            found.append((count, text.strip()[:60]))
    return found


CASES = {
    "text beside a nested paragraph": "<blockquote>Before<p>In</p>After</blockquote>",
    "text beside a list": "<div>Before<ul><li>Item</li></ul>After</div>",
    "nested blockquotes": "<blockquote>Outer <blockquote>Inner</blockquote> tail</blockquote>",
    "list inside a span": "<span><ol><li><p>Item</p></li></ol></span><p>After</p>",
    "inline element beside a block": "<div><em>loose</em><p>para</p></div>",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_text_node_is_in_one_unit(name: str) -> None:
    markup = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
        f"<body>{CASES[name]}</body></html>"
    )
    assert _misplaced_text(parse_xhtml(markup.encode()).root) == []


def test_text_directly_in_body_is_reported() -> None:
    markup = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
        "<body>Loose words <a href='#x'>a link</a><p>Para.</p></body></html>"
    )
    assert loose_body_text(parse_xhtml(markup.encode()).root) == 2


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_text_is_covered(name: str, tmp_path: Path) -> None:
    book = FIXTURES[name](tmp_path / f"{name}.epub")
    package = read_package(book)
    with zipfile.ZipFile(book) as archive:
        for item in package.manifest.values():
            if item.media_type == "application/xhtml+xml":
                assert _misplaced_text(parse_xhtml(archive.read(item.path)).root) == [], item.path


CORPUS = os.environ.get("TEPUB_CORPUS_DIR")


@pytest.mark.corpus
@pytest.mark.skipif(not CORPUS, reason="set TEPUB_CORPUS_DIR to a folder of real EPUBs")
def test_corpus_text_is_covered() -> None:
    problems = []
    for book in sorted(Path(CORPUS).expanduser().glob("*.epub")):
        package = read_package(book)
        with zipfile.ZipFile(book) as archive:
            for item in package.manifest.values():
                if item.media_type != "application/xhtml+xml" or "nav" in item.properties:
                    continue
                misplaced = _misplaced_text(parse_xhtml(archive.read(item.path)).root)
                if misplaced:
                    problems.append((book.stem[:30], item.path, misplaced[:2]))
    assert problems == []
