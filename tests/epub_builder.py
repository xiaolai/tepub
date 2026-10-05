"""Build small, valid EPUB files for tests.

Tests that exercise reading or writing need a real container, not a Mock: the
footnote filter passed its whole suite for months against a mocked reader method
that did not exist.
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

MEDIA_TYPES = {
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def xhtml(title: str, body: str, *, version: int = 3, lang: str = "en", css: str | None = None) -> str:
    """Wrap body markup in a complete XHTML content document."""
    link = f'<link rel="stylesheet" type="text/css" href="{escape(css)}"/>' if css else ""
    if version == 2:
        head = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
            '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}">\n'
        )
    else:
        head = (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
        )
    return f"{head}<head><title>{escape(title)}</title>{link}</head>\n<body>\n{body}\n</body>\n</html>\n"


def _relative(from_doc: str, target: str) -> str:
    """Href from one OEBPS-relative document to another."""
    start = PurePosixPath(from_doc).parent
    parts_from = [p for p in start.parts if p not in ("", ".")]
    parts_to = list(PurePosixPath(target).parts)
    while parts_from and parts_to and parts_from[0] == parts_to[0]:
        parts_from.pop(0)
        parts_to.pop(0)
    return "/".join([".."] * len(parts_from) + parts_to)


def build_epub(
    path: Path,
    chapters: list[tuple[str, str, str]],
    title: str = "Fixture",
    *,
    version: int = 3,
    lang: str = "en",
    css: str | None = None,
    resources: dict[str, bytes] | None = None,
    page_direction: str | None = None,
    properties: dict[str, str] | None = None,
    nav_in_spine: bool = False,
) -> Path:
    """Write an EPUB to ``path``.

    ``chapters`` is a list of (file name, title, body markup); names are relative
    to OEBPS and may contain subdirectories. Every chapter is in the spine and
    the table of contents, in order: a nav document for EPUB 3, an NCX for
    EPUB 2. ``css`` is a stylesheet linked from every chapter; ``resources``
    adds files such as images, by OEBPS-relative name; ``properties`` gives
    manifest properties per chapter, such as "svg" or "mathml".
    """
    properties = properties or {}
    resources = dict(resources or {})
    if css is not None:
        resources["style.css"] = css.encode("utf-8")

    manifest: list[str] = []
    if version == 3:
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    else:
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    for index, name in enumerate(sorted(resources), start=1):
        suffix = PurePosixPath(name).suffix
        manifest.append(f'<item id="r{index}" href="{escape(name)}" media-type="{MEDIA_TYPES[suffix]}"/>')

    spine: list[str] = ['<itemref idref="nav"/>'] if nav_in_spine and version == 3 else []
    toc_entries: list[tuple[str, str]] = []
    for index, (name, chapter_title, _body) in enumerate(chapters, start=1):
        props = f' properties="{properties[name]}"' if name in properties else ""
        manifest.append(
            f'<item id="c{index}" href="{escape(name)}" media-type="application/xhtml+xml"{props}/>'
        )
        spine.append(f'<itemref idref="c{index}"/>')
        if chapter_title:  # an empty title keeps a chapter out of the TOC
            toc_entries.append((name, chapter_title))

    uid = "urn:uuid:00000000-0000-4000-8000-000000000000"
    if version == 3:
        metadata = (
            f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{escape(title)}</dc:title>'
            f'<dc:language>{lang}</dc:language>'
            '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        )
        spine_open = "<spine" + (f' page-progression-direction="{page_direction}"' if page_direction else "") + ">"
    else:
        metadata = (
            f'<dc:identifier id="uid" opf:scheme="uuid">{uid}</dc:identifier>'
            f'<dc:title>{escape(title)}</dc:title><dc:language>{lang}</dc:language>'
        )
        spine_open = '<spine toc="ncx">'
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{'3.0' if version == 3 else '2.0'}" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    {metadata}
  </metadata>
  <manifest>
    {chr(10).join(manifest)}
  </manifest>
  {spine_open}
    {chr(10).join(spine)}
  </spine>
</package>
"""
    files: dict[str, str | bytes] = {"content.opf": opf}
    if version == 3:
        items = "".join(
            f'<li><a href="{escape(name)}">{escape(t)}</a></li>' for name, t in toc_entries
        )
        files["nav.xhtml"] = xhtml(
            "Contents", f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{items}</ol></nav>', lang=lang
        )
    else:
        points = "".join(
            f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
            f'<content src="{escape(name)}"/></navPoint>'
            for i, (name, t) in enumerate(toc_entries, start=1)
        )
        files["toc.ncx"] = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="1"/>'
            '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
            f"<docTitle><text>{escape(title)}</text></docTitle><navMap>{points}</navMap></ncx>\n"
        )
    for name, chapter_title, body in chapters:
        link = _relative(name, "style.css") if css is not None else None
        files[name] = xhtml(chapter_title or name, body, version=version, lang=lang, css=link)
    files.update(resources)

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            archive.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_DEFLATED)
    return path
