"""Build small, valid EPUB 3 files for tests.

Tests that exercise reading or writing need a real container, not a Mock: the
footnote filter passed its whole suite for months against a mocked reader method
that did not exist.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

XHTML_HEAD = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><title>{title}</title></head>
<body>
"""
XHTML_TAIL = "</body>\n</html>\n"

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def xhtml(title: str, body: str) -> str:
    """Wrap body markup in a complete XHTML content document."""
    return XHTML_HEAD.format(title=escape(title)) + body + "\n" + XHTML_TAIL


def build_epub(path: Path, chapters: list[tuple[str, str, str]], title: str = "Fixture") -> Path:
    """Write an EPUB 3 to ``path``.

    ``chapters`` is a list of (file name, title, body markup); file names are
    relative to the OEBPS directory and may contain subdirectories. Every
    chapter is in the spine and in the navigation document, in order.
    """
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    nav_items = []
    for index, (name, chapter_title, _body) in enumerate(chapters, start=1):
        manifest.append(
            f'<item id="c{index}" href="{escape(name)}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="c{index}"/>')
        nav_items.append(f'<li><a href="{escape(name)}">{escape(chapter_title)}</a></li>')

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000000</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    {chr(10).join(manifest)}
  </manifest>
  <spine>
    {chr(10).join(spine)}
  </spine>
</package>
"""
    nav = xhtml(
        "Contents",
        '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
        + "".join(nav_items)
        + "</ol></nav>",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        for name, chapter_title, body in chapters:
            archive.writestr(
                f"OEBPS/{name}", xhtml(chapter_title, body), compress_type=zipfile.ZIP_DEFLATED
            )
    return path
