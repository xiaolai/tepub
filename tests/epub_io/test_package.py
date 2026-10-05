"""Spine, table of contents and metadata, read from the package without ebooklib.

ebooklib crashed with IndexError on a navigation document that had landmarks
but no toc nav, and its get_content() rebuilt documents; the reader now uses
only the container module.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from epub_io.container import read_package
from tests.epub_builder import build_epub


def _titles(entries, depth=0):
    out = []
    for entry in entries:
        out.append(("  " * depth) + f"{entry.title} -> {entry.href}")
        out.extend(_titles(entry.children, depth + 1))
    return out


def test_epub3_nav_gives_titles_and_package_relative_hrefs(tmp_path: Path) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [("text/ch1.xhtml", "One", "<h1 id='a'>One</h1>"), ("text/ch2.xhtml", "Two", "<p>x</p>")],
    )
    package = read_package(book)
    assert _titles(package.toc) == ["One -> text/ch1.xhtml", "Two -> text/ch2.xhtml"]
    assert [s.href.as_posix() for s in package.spine_items()] == ["text/ch1.xhtml", "text/ch2.xhtml"]


def test_epub2_ncx_is_read(tmp_path: Path) -> None:
    book = build_epub(
        tmp_path / "b.epub", [("text/ch1.xhtml", "One", "<p>1</p>"), ("text/ch2.xhtml", "Two", "<p>2</p>")], version=2
    )
    assert _titles(read_package(book).toc) == ["One -> text/ch1.xhtml", "Two -> text/ch2.xhtml"]


def test_nested_nav_in_a_subfolder_with_fragments_and_encoded_names(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "b.epub", [("text/my ch.xhtml", "Ch", "<h1 id='s1'>A</h1><h2 id='s2'>B</h2>")])
    nav = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>t</title></head><body>'
        '<nav epub:type="landmarks"><ol><li><a epub:type="bodymatter" href="../text/my%20ch.xhtml">Start</a></li></ol></nav>'
        '<nav epub:type="toc"><ol><li><a href="../text/my%20ch.xhtml#s1">Part</a>'
        '<ol><li><a href="../text/my%20ch.xhtml#s2">Section</a></li></ol></li>'
        "<li><span>Heading only</span></li></ol></nav></body></html>"
    )
    _move_nav(book, "OEBPS/nav/nav.xhtml", nav)
    toc = read_package(book).toc
    assert _titles(toc) == [
        "Part -> text/my ch.xhtml#s1",
        "  Section -> text/my ch.xhtml#s2",
        "Heading only -> None",
    ]


def test_a_nav_with_landmarks_only_falls_back_to_the_ncx_or_nothing(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "b.epub", [("ch1.xhtml", "One", "<p>1</p>")])
    nav = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>t</title></head><body>'
        '<nav epub:type="landmarks"><ol><li><a href="ch1.xhtml">Start</a></li></ol></nav></body></html>'
    )
    _move_nav(book, "OEBPS/nav.xhtml", nav)
    assert read_package(book).toc == []  # ebooklib raised IndexError here


def test_metadata(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "b.epub", [("ch1.xhtml", "One", "<p>1</p>")], title="Moby-Dick")
    meta = read_package(book).metadata
    assert meta.title == "Moby-Dick" and meta.language == "en"


def _move_nav(book: Path, new_path: str, content: str) -> None:
    """Replace the book's nav document, possibly at a new path."""
    with zipfile.ZipFile(book) as archive:
        entries = [(i, archive.read(i)) for i in archive.infolist()]
    with zipfile.ZipFile(book, "w") as archive:
        for info, data in entries:
            if info.filename == "OEBPS/nav.xhtml":
                continue
            if info.filename == "OEBPS/content.opf":
                href = new_path.removeprefix("OEBPS/")
                data = data.replace(b'href="nav.xhtml"', f'href="{href}"'.encode())
            archive.writestr(info, data)
        archive.writestr(new_path, content)


def test_a_file_that_is_not_a_zip_is_named_as_not_an_epub(tmp_path) -> None:
    import pytest

    from epub_io.container import EpubStructureError, read_package

    book = tmp_path / "book.epub"
    book.write_bytes(b"\x00\x01 not a zip archive at all")
    with pytest.raises(EpubStructureError, match="not a zip archive"):
        read_package(book)


def test_the_cli_reports_a_damaged_epub_without_a_traceback(tmp_path) -> None:
    import subprocess
    import sys

    book = tmp_path / "book.epub"
    book.write_bytes(b"\x00\x01 not a zip archive at all")
    done = subprocess.run(
        [sys.executable, "-c", "from cli.main import run; run()", "extract", str(book)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    output = done.stdout + done.stderr
    assert done.returncode == 1
    assert "not a zip archive" in output and "Traceback" not in output
