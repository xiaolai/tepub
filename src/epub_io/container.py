"""Read an EPUB's container and package, and write a copy with some entries replaced.

The writer used ebooklib, which rebuilds the book on save: every content
document's <head> is regenerated (stylesheet links lost), every file moves into
EPUB/, the navigation document is regenerated, and the spine gains a reference
to an NCX that does not exist. With nothing changed, it added epubcheck errors
to 15 of 19 real books.

Here the output is the input's zip, entry for entry in the same order, with the
same names, bytes and compression, except the entries a caller replaces.
"""

from __future__ import annotations

import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from exceptions import TepubError

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
MIMETYPE = b"application/epub+zip"


class EpubStructureError(TepubError):
    """The EPUB's container or package cannot be read safely."""


def secure_xml_parser(**options) -> etree.XMLParser:
    """An XML parser that never resolves entities, loads DTDs or touches the network."""
    return etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, **options
    )


@dataclass(frozen=True)
class ManifestItem:
    id: str
    href: str  # relative to the package document, as written in the OPF
    path: str  # zip entry name
    media_type: str
    properties: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SpineItem:
    idref: str
    linear: bool


@dataclass(frozen=True)
class SpineDocument:
    """A spine entry resolved through the manifest."""

    index: int
    idref: str
    href: Path  # relative to the package document
    media_type: str
    linear: bool


@dataclass
class TocEntry:
    title: str
    href: str | None  # relative to the package document, fragment kept
    children: list[TocEntry] = field(default_factory=list)


@dataclass
class Metadata:
    title: str | None = None
    creators: list[str] = field(default_factory=list)
    publisher: str | None = None
    date: str | None = None
    language: str | None = None
    cover_id: str | None = None


@dataclass
class Package:
    opf_path: str
    version: str
    manifest: dict[str, ManifestItem]
    spine: list[SpineItem]
    spine_toc: str | None
    entries: list[zipfile.ZipInfo] = field(repr=False)
    metadata: Metadata = field(default_factory=Metadata)
    toc: list[TocEntry] = field(default_factory=list)

    def spine_items(self) -> list[SpineDocument]:
        """Spine entries whose manifest item exists, in reading order."""
        found = []
        for index, entry in enumerate(self.spine):
            item = self.manifest.get(entry.idref)
            if item is None:
                continue
            found.append(
                SpineDocument(
                    index=index,
                    idref=entry.idref,
                    href=Path(self.package_href(item.path)),
                    media_type=item.media_type,
                    linear=entry.linear,
                )
            )
        return found

    def package_href(self, zip_path: str) -> str:
        """A zip entry name expressed relative to the package document."""
        return posixpath.relpath(zip_path, self.opf_dir or ".")

    def cover_item(self) -> ManifestItem | None:
        for item in self.manifest.values():
            if "cover-image" in item.properties:
                return item
        if self.metadata.cover_id:
            return self.manifest.get(self.metadata.cover_id)
        return None

    @property
    def opf_dir(self) -> str:
        return posixpath.dirname(self.opf_path)

    def item_by_path(self, path: str) -> ManifestItem | None:
        for item in self.manifest.values():
            if item.path == path:
                return item
        return None

    def nav_item(self) -> ManifestItem | None:
        for item in self.manifest.values():
            if "nav" in item.properties:
                return item
        return None

    def ncx_item(self) -> ManifestItem | None:
        if self.spine_toc and self.spine_toc in self.manifest:
            return self.manifest[self.spine_toc]
        for item in self.manifest.values():
            if item.media_type == "application/x-dtbncx+xml":
                return item
        return None

    def zip_path(self, href: str) -> str:
        """Resolve an OPF-relative href to a zip entry name."""
        return resolve(self.opf_path, href)


def resolve(from_entry: str, href: str) -> str:
    """Resolve ``href`` relative to the zip entry ``from_entry``, without its fragment."""
    target = unquote(href.split("#", 1)[0])
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(from_entry), target))
    if joined.startswith("../") or joined == ".." or posixpath.isabs(joined):
        raise EpubStructureError(f"{href!r} in {from_entry} points outside the book")
    return joined


def _check_entry_names(entries: list[zipfile.ZipInfo]) -> None:
    """Refuse duplicate names, and names that differ only by case.

    Either makes "copy every entry" ambiguous: zip readers disagree on which
    duplicate wins, and case-insensitive file systems merge the other kind.
    """
    exact: set[str] = set()
    folded: dict[str, str] = {}
    for info in entries:
        if info.filename in exact:
            raise EpubStructureError(f"the zip has two entries named {info.filename!r}")
        exact.add(info.filename)
        key = info.filename.casefold()
        if key in folded:
            raise EpubStructureError(
                f"the zip has entries {folded[key]!r} and {info.filename!r}, which differ "
                "only by case; they cannot be told apart on most file systems"
            )
        folded[key] = info.filename


def read_package(epub_path: Path) -> Package:
    try:
        archive_file = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile as exc:
        raise EpubStructureError(
            f"{epub_path.name} is not an EPUB: it is not a zip archive. It may be "
            "damaged, or protected by DRM."
        ) from exc
    with archive_file as archive:
        entries = archive.infolist()
        _check_entry_names(entries)
        try:
            container = etree.fromstring(
                archive.read("META-INF/container.xml"), parser=secure_xml_parser()
            )
        except KeyError as exc:
            raise EpubStructureError(f"{epub_path.name} has no META-INF/container.xml") from exc
        rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise EpubStructureError(f"{epub_path.name}: container.xml names no package document")
        opf_path = rootfile.get("full-path")
        try:
            opf = etree.fromstring(archive.read(opf_path), parser=secure_xml_parser())
        except KeyError as exc:
            raise EpubStructureError(f"{epub_path.name}: package {opf_path} is missing") from exc

    manifest: dict[str, ManifestItem] = {}
    for node in opf.iterfind(f"{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
        href = node.get("href", "")
        manifest[node.get("id", "")] = ManifestItem(
            id=node.get("id", ""),
            href=href,
            path=resolve(opf_path, href),
            media_type=node.get("media-type", ""),
            properties=frozenset((node.get("properties") or "").split()),
        )
    spine_node = opf.find(f"{{{OPF_NS}}}spine")
    spine = [
        SpineItem(idref=n.get("idref", ""), linear=n.get("linear", "yes") != "no")
        for n in (spine_node.iterfind(f"{{{OPF_NS}}}itemref") if spine_node is not None else [])
    ]
    package = Package(
        opf_path=opf_path,
        version=opf.get("version", ""),
        manifest=manifest,
        spine=spine,
        spine_toc=spine_node.get("toc") if spine_node is not None else None,
        entries=entries,
        metadata=_metadata(opf),
    )
    with zipfile.ZipFile(epub_path) as archive:
        package.toc = _read_toc(package, archive)
    return package


def metadata_summary(metadata: Metadata) -> dict[str, str | None]:
    """Title, first author, publisher and year, as extraction records them."""
    year = metadata.date
    if year and len(year) >= 4 and year[:4].isdigit():
        year = year[:4]
    return {
        "title": metadata.title,
        "author": metadata.creators[0] if metadata.creators else None,
        "publisher": metadata.publisher,
        "year": year,
    }


def iter_toc(entries: list[TocEntry]) -> Iterator[TocEntry]:
    """Every entry of a table of contents, depth first, in reading order."""
    for entry in entries:
        yield entry
        yield from iter_toc(entry.children)


def _first_text(parent: etree._Element, tag: str) -> str | None:
    node = parent.find(tag)
    text = (node.text or "").strip() if node is not None else ""
    return text or None


def _metadata(opf: etree._Element) -> Metadata:
    meta = opf.find(f"{{{OPF_NS}}}metadata")
    if meta is None:
        return Metadata()
    cover_id = None
    for node in meta.iterfind(f"{{{OPF_NS}}}meta"):
        if node.get("name") == "cover":
            cover_id = node.get("content")
    return Metadata(
        title=_first_text(meta, f"{{{DC_NS}}}title"),
        creators=[
            (n.text or "").strip()
            for n in meta.iterfind(f"{{{DC_NS}}}creator")
            if (n.text or "").strip()
        ],
        publisher=_first_text(meta, f"{{{DC_NS}}}publisher"),
        date=_first_text(meta, f"{{{DC_NS}}}date"),
        language=_first_text(meta, f"{{{DC_NS}}}language"),
        cover_id=cover_id,
    )


def _toc_href(package: Package, toc_path: str, href: str | None) -> str | None:
    if not href:
        return None
    path, _, fragment = href.partition("#")
    target = resolve(toc_path, path) if path else toc_path
    relative = package.package_href(target)
    return f"{relative}#{fragment}" if fragment else relative


def _label(element: etree._Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _nav_entries(package: Package, nav_path: str, ol: etree._Element) -> list[TocEntry]:
    entries = []
    for li in ol.iterfind(f"{{{XHTML_NS}}}li"):
        head = li.find(f"{{{XHTML_NS}}}a")
        if head is None:
            head = li.find(f"{{{XHTML_NS}}}span")
        if head is None:
            continue
        nested = li.find(f"{{{XHTML_NS}}}ol")
        entries.append(
            TocEntry(
                title=_label(head),
                href=_toc_href(package, nav_path, head.get("href")),
                children=_nav_entries(package, nav_path, nested) if nested is not None else [],
            )
        )
    return entries


def _ncx_entries(package: Package, ncx_path: str, parent: etree._Element) -> list[TocEntry]:
    entries = []
    for point in parent.iterfind(f"{{{NCX_NS}}}navPoint"):
        label = point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
        content = point.find(f"{{{NCX_NS}}}content")
        entries.append(
            TocEntry(
                title=" ".join((label.text or "").split()) if label is not None else "",
                href=_toc_href(
                    package, ncx_path, content.get("src") if content is not None else None
                ),
                children=_ncx_entries(package, ncx_path, point),
            )
        )
    return entries


def _read_toc(package: Package, archive: zipfile.ZipFile) -> list[TocEntry]:
    """The book's table of contents: the EPUB 3 nav, else the NCX, else nothing.

    A nav without a toc section (landmarks only) is not an error; ebooklib
    raised IndexError on it.
    """
    nav = package.nav_item()
    if nav is not None:
        root = etree.fromstring(archive.read(nav.path), parser=secure_xml_parser())
        for element in root.iter(f"{{{XHTML_NS}}}nav"):
            if "toc" in (element.get(f"{{{OPS_NS}}}type") or "").split():
                ol = element.find(f"{{{XHTML_NS}}}ol")
                if ol is not None:
                    return _nav_entries(package, nav.path, ol)
    ncx = package.ncx_item()
    if ncx is not None:
        root = etree.fromstring(archive.read(ncx.path), parser=secure_xml_parser())
        nav_map = root.find(f"{{{NCX_NS}}}navMap")
        if nav_map is not None:
            return _ncx_entries(package, ncx.path, nav_map)
    return []


def write_copy(source: Path, output: Path, replacements: dict[str, bytes]) -> None:
    """Write ``source`` to ``output`` with the named entries' bytes replaced.

    Every other entry keeps its name, position, bytes and compression type. The
    mimetype entry is written first and stored, as the format requires.
    """
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        _check_entry_names(entries)
        names = {info.filename for info in entries}
        unknown = sorted(set(replacements) - names)
        if unknown:
            raise EpubStructureError(f"cannot replace entries the book does not have: {unknown}")

        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + ".tmp")
        with zipfile.ZipFile(tmp, "w") as out:
            mimetype = next((i for i in entries if i.filename == "mimetype"), None)
            data = archive.read(mimetype) if mimetype is not None else MIMETYPE
            out.writestr(
                zipfile.ZipInfo("mimetype", date_time=_date(mimetype)),
                data,
                compress_type=zipfile.ZIP_STORED,
            )
            for info in entries:
                if info.filename == "mimetype":
                    continue
                payload = replacements.get(info.filename)
                if payload is None:
                    payload = archive.read(info)
                copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                # The attributes mean what the creating system says they mean:
                # a FAT book's zero attributes, relabelled as Unix, became
                # Python's stand-in mode 600, and folders unzipped unopenable.
                copy.create_system = info.create_system
                copy.external_attr = info.external_attr
                copy.compress_type = info.compress_type
                out.writestr(copy, payload)
        tmp.replace(output)


def _date(info: zipfile.ZipInfo | None) -> tuple[int, int, int, int, int, int]:
    return info.date_time if info is not None else (1980, 1, 1, 0, 0, 0)
