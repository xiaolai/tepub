from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree

from epub_io.reader import EpubReader
from epub_io.resources import get_item_by_href
from epub_io.path_utils import normalize_epub_href


@dataclass
class SpineCoverCandidate:
    href: Path
    document_href: Path


_IMAGE_ATTRS = (
    "src",
    "href",
    "{http://www.w3.org/1999/xlink}href",
)




def find_spine_cover_candidate(reader: EpubReader) -> SpineCoverCandidate | None:
    for document in reader.iter_documents():
        tree = document.tree
        if tree is None:
            continue
        for element in tree.iter():
            try:
                tag_name = etree.QName(element.tag).localname
            except (ValueError, AttributeError):
                continue
            if tag_name not in {"img", "image"}:
                continue
            href_value = None
            for attr in _IMAGE_ATTRS:
                href_value = element.get(attr)
                if href_value:
                    break
            candidate_href_str = normalize_epub_href(document.path, href_value or "")
            if not candidate_href_str:
                continue
            try:
                get_item_by_href(reader.book, Path(candidate_href_str))
            except KeyError:
                continue
            return SpineCoverCandidate(href=Path(candidate_href_str), document_href=document.path)
    return None
