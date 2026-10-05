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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from exceptions import TepubError

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
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


@dataclass
class Package:
    opf_path: str
    version: str
    manifest: dict[str, ManifestItem]
    spine: list[SpineItem]
    spine_toc: str | None
    entries: list[zipfile.ZipInfo] = field(repr=False)

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
    with zipfile.ZipFile(epub_path) as archive:
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
    return Package(
        opf_path=opf_path,
        version=opf.get("version", ""),
        manifest=manifest,
        spine=spine,
        spine_toc=spine_node.get("toc") if spine_node is not None else None,
        entries=entries,
    )


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
            out.writestr(zipfile.ZipInfo("mimetype", date_time=_date(mimetype)), data,
                         compress_type=zipfile.ZIP_STORED)
            for info in entries:
                if info.filename == "mimetype":
                    continue
                payload = replacements.get(info.filename)
                if payload is None:
                    payload = archive.read(info)
                copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                copy.external_attr = info.external_attr
                copy.compress_type = info.compress_type
                out.writestr(copy, payload)
        tmp.replace(output)


def _date(info: zipfile.ZipInfo | None) -> tuple[int, int, int, int, int, int]:
    return info.date_time if info is not None else (1980, 1, 1, 0, 0, 0)
