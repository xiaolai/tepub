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
from epub_io.writer import _relabel_document, write_updated_epub
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
        [
            ("text/ch1.xhtml", "Original One", "<h1>One</h1>"),
            ("text/ch2.xhtml", "Original Two", "<h1>Two</h1>"),
        ],
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


def test_translated_only_leaves_stylesheets_alone(tmp_path: Path) -> None:
    """Nothing is marked as an original in translated-only output, so there is
    nothing for a hiding rule to hide; the publisher's CSS is not edited."""
    book = build_epub(
        tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")], css="p { color: black; }"
    )
    out = tmp_path / "out.epub"
    write_updated_epub(book, out, {}, css_mode="translated_only")
    assert _read(out, "OEBPS/style.css") == _read(book, "OEBPS/style.css")


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


def test_entries_keep_the_system_their_attributes_belong_to(tmp_path: Path) -> None:
    """A FAT-made book's attributes were relabelled as Unix, where Python's
    stand-in for zero attributes reads as mode 600: folders unzipped with no
    permission to open them."""
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")])
    fat = tmp_path / "fat.epub"
    with zipfile.ZipFile(book) as source, zipfile.ZipFile(fat, "w") as out:
        for info in source.infolist():
            data = source.read(info)
            info.create_system, info.external_attr = 0, 0
            out.writestr(info, data)
        folder = zipfile.ZipInfo("OEBPS/")
        folder.create_system, folder.external_attr = 0, 0x10
        out.writestr(folder, b"")
    out = tmp_path / "out.epub"

    write_updated_epub(fat, out, {})

    with zipfile.ZipFile(out) as archive:
        entries = {info.filename: info for info in archive.infolist()}
    assert {i.create_system for n, i in entries.items() if n != "mimetype"} == {0}
    assert entries["OEBPS/"].external_attr & 0x10


def test_translated_only_matches_an_entry_by_its_source_label(tmp_path: Path) -> None:
    """The fragment often names a <section>, not the heading; the label still does."""
    book = build_epub(
        tmp_path / "in.epub",
        [
            ("text/ch1.xhtml", "Original One", "<h1>One</h1>"),
            ("text/ch2.xhtml", "Original Two", "<h1>Two</h1>"),
        ],
    )
    out = tmp_path / "out.epub"

    write_updated_epub(
        book,
        out,
        {},
        toc_updates={
            PurePosixPath("text/ch1.xhtml"): {"text:original one": "第一章"},
            PurePosixPath("text/ch2.xhtml"): {"text:original two": "第二章"},
        },
        css_mode="translated_only",
    )

    nav = _read(out, "OEBPS/nav.xhtml").decode("utf-8")
    assert "第一章" in nav and "第二章" in nav
    assert "Original One" not in nav and "Original Two" not in nav
    etree.fromstring(_read(out, "OEBPS/nav.xhtml"))


def test_translated_only_declares_the_target_language(tmp_path: Path) -> None:
    book = build_epub(
        tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")], lang="en"
    )
    out = tmp_path / "out.epub"

    write_updated_epub(
        book, out, {}, css_mode="translated_only", target_language="zh-CN"
    )

    opf = _read(out, "OEBPS/content.opf").decode("utf-8")
    assert opf.count("<dc:language>") == 1
    assert "<dc:language>zh-CN</dc:language>" in opf
    document = _read(out, "OEBPS/ch1.xhtml").decode("utf-8")
    assert 'lang="zh-CN"' in document and 'xml:lang="zh-CN"' in document


def test_bilingual_declares_both_languages_and_leaves_documents_alone(
    tmp_path: Path,
) -> None:
    book = build_epub(
        tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")], lang="en"
    )
    out = tmp_path / "out.epub"

    write_updated_epub(
        book,
        out,
        {},
        css_mode="bilingual",
        source_language="en",
        target_language="zh-CN",
    )

    opf = _read(out, "OEBPS/content.opf").decode("utf-8")
    assert "<dc:language>en</dc:language>" in opf
    assert "<dc:language>zh-CN</dc:language>" in opf
    assert _read(out, "OEBPS/ch1.xhtml") == _read(book, "OEBPS/ch1.xhtml")


def test_the_export_keeps_the_source_file_mode(tmp_path: Path) -> None:
    """mkstemp creates 0600; a 0644 book must not come out private."""
    book = build_epub(tmp_path / "in.epub", [("ch1.xhtml", "One", "<p>One.</p>")])
    book.chmod(0o640)
    out = tmp_path / "out.epub"

    write_updated_epub(book, out, {})

    assert out.stat().st_mode & 0o777 == 0o640


def test_single_quoted_language_attributes_do_not_become_duplicates() -> None:
    relabelled = _relabel_document(
        b"<html lang='en' xml:lang='en'><body><p>x</p></body></html>", "zh-CN"
    )
    assert relabelled.count(b"lang=") == 2
    assert b"'en'" not in relabelled
    assert b'lang="zh-CN"' in relabelled and b'xml:lang="zh-CN"' in relabelled


def test_whitespace_around_the_equals_does_not_leave_a_duplicate_language() -> None:
    """XML allows ``lang = "en"``, with spaces or newlines around the sign."""
    relabelled = _relabel_document(
        b'<html lang = "en" xml:lang\n=\n\'en\'><body><p>x</p></body></html>', "zh-CN"
    )
    assert relabelled.count(b"lang=") == 2
    assert b'"en"' not in relabelled and b"'en'" not in relabelled
    assert relabelled.count(b'lang="zh-CN"') == 2


def test_a_greater_than_inside_another_root_attribute_is_not_a_tag_end() -> None:
    """A quoted ">" is valid in an attribute value. A tag regex that stops at
    the first ">" cuts the root tag short, leaving a malformed document."""
    source = (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
        '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
        '<html lang="en" data-note="a > b"><body><p>x</p></body></html>'
    )

    relabelled = _relabel_document(source.encode(), "zh-CN")
    text = relabelled.decode()

    assert text.startswith(
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
        '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html '
    )
    assert text.count("lang=") == 2
    assert 'data-note="a > b"' in text
    assert 'lang="zh-CN"' in text and 'xml:lang="zh-CN"' in text
    # Duplicate or truncated attributes would make this parse fail.
    assert etree.fromstring(relabelled).get("lang") == "zh-CN"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_a_bom_less_utf16_document_is_read_in_its_own_encoding(encoding: str) -> None:
    """A BOM-less UTF-16 document declares UTF-16, but those declaration bytes
    do not look like ASCII, so decoding it as UTF-8 crashed."""
    source = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<html lang="en"><body><p>x</p></body></html>'
    ).encode(encoding)

    relabelled = _relabel_document(source, "zh-CN")

    text = relabelled.decode(encoding)
    assert text.count("lang=") == 2
    assert 'lang="zh-CN"' in text and 'xml:lang="zh-CN"' in text
