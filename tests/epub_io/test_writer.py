"""The writer replaces what it is given and copies everything else.

These replace tests that mocked ebooklib's internals: they asserted on objects
the real writer no longer has, and passed whatever the output file contained.
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

import pytest
from lxml import etree

from epub_io.container import EpubStructureError
from epub_io.writer import write_updated_epub
from tests.epub_builder import build_epub


def _read(epub: Path, name: str) -> bytes:
    with zipfile.ZipFile(epub) as archive:
        return archive.read(name)


def test_only_the_given_documents_change(tmp_path: Path) -> None:
    book = build_epub(
        tmp_path / "in.epub",
        [("ch1.xhtml", "One", "<p>One.</p>"), ("ch2.xhtml", "Two", "<p>Two.</p>")],
        css="p { color: black; }",
    )
    new = b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head><body><p>Un.</p></body></html>'
    out = tmp_path / "out.epub"

    write_updated_epub(book, out, {Path("ch1.xhtml"): new})

    assert _read(out, "OEBPS/ch1.xhtml") == new
    for name in ("OEBPS/ch2.xhtml", "OEBPS/style.css", "OEBPS/nav.xhtml", "OEBPS/content.opf"):
        assert _read(out, name) == _read(book, name), name


@pytest.mark.parametrize("version", [2, 3])
def test_translated_only_retitles_the_table_of_contents(tmp_path: Path, version: int) -> None:
    book = build_epub(
        tmp_path / "in.epub",
        [("text/ch1.xhtml", "Original One", "<h1>One</h1>"), ("text/ch2.xhtml", "Original Two", "<h1>Two</h1>")],
        version=version,
    )
    out = tmp_path / "out.epub"

    write_updated_epub(
        book,
        out,
        {},
        toc_updates={PurePosixPath("text/ch1.xhtml"): {None: "第一章"}},
        css_mode="translated_only",
    )

    toc = "OEBPS/nav.xhtml" if version == 3 else "OEBPS/toc.ncx"
    text = _read(out, toc).decode("utf-8")
    assert "第一章" in text and "Original One" not in text
    assert "Original Two" in text  # untouched entries keep their titles
    etree.fromstring(_read(out, toc))  # still well-formed


def test_translated_only_adds_the_hiding_css_once(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")], css="p { color: black; }")
    out = tmp_path / "out.epub"
    write_updated_epub(book, out, {}, css_mode="translated_only")
    css = _read(out, "OEBPS/style.css").decode("utf-8")
    assert css.startswith("p { color: black; }")
    assert css.count('[data-lang="original"]') == 1


def test_bilingual_output_leaves_toc_and_css_alone(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")], css="p {}")
    out = tmp_path / "out.epub"
    write_updated_epub(book, out, {}, toc_updates={PurePosixPath("ch1.xhtml"): {None: "X"}})
    assert _read(out, "OEBPS/nav.xhtml") == _read(book, "OEBPS/nav.xhtml")
    assert _read(out, "OEBPS/style.css") == _read(book, "OEBPS/style.css")


@pytest.mark.filterwarnings("ignore:Duplicate name")  # the duplicate is the point
@pytest.mark.parametrize("second", ["OEBPS/ch1.xhtml", "OEBPS/CH1.xhtml"])
def test_duplicate_or_case_colliding_entries_are_refused(tmp_path: Path, second: str) -> None:
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")])
    with zipfile.ZipFile(book, "a") as archive:
        archive.writestr(second, b"<html/>")
    with pytest.raises(EpubStructureError):
        write_updated_epub(book, tmp_path / "out.epub", {})


def test_replacing_a_missing_document_is_an_error(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")])
    with pytest.raises(EpubStructureError, match="does not have"):
        write_updated_epub(book, tmp_path / "out.epub", {Path("nope.xhtml"): b"x"})
