"""Parse and serialise EPUB content documents as XML.

They were parsed with lxml's HTML parser, which lowercased SVG names (viewBox
became viewbox, breaking SVG covers), turned prefixed names such as epub:switch
into literal tag names that no XPath could address, and dropped the XHTML 1.1
DOCTYPE. All 10,589 content documents in a 19-book corpus parse as XML, so a
document that does not is reported rather than repaired: repairing with an HTML
parser is what caused the damage.
"""

from __future__ import annotations

import html.entities
import re
from dataclasses import dataclass

from lxml import etree

from exceptions import TepubError

from .container import secure_xml_parser

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_XML_PREDEFINED = {"lt", "gt", "amp", "quot", "apos"}
# Comments and CDATA sections are copied untouched by the entity pre-pass.
_OPAQUE = re.compile(rb"(<!--.*?-->|<!\[CDATA\[.*?\]\]>)", re.S)
_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9]*);")


class NotWellFormedError(TepubError):
    """A content document is not well-formed XML; it is left untranslated."""


@dataclass
class XhtmlDocument:
    root: etree._Element
    xml_declaration: bool
    encoding: str
    doctype: str | None

    @property
    def body(self) -> etree._Element | None:
        return self.root.find(f"{{{XHTML_NS}}}body")


def _numeric_reference(match: re.Match[bytes]) -> bytes:
    name = match.group(1).decode("ascii")
    if name in _XML_PREDEFINED:
        return match.group(0)
    chars = html.entities.html5.get(f"{name};")
    if chars is None:
        # Not an HTML entity: an XML parser resolves or rejects it on its own
        # terms, and resolution of declared entities is switched off.
        return match.group(0)
    return b"".join(f"&#{ord(char)};".encode("ascii") for char in chars)


def _replace_html_entities(data: bytes) -> bytes:
    """Rewrite HTML named entities such as &nbsp; as numeric references.

    XML knows only five named entities; EPUB 2 books in particular use HTML's
    others. Comments and CDATA sections are left as they are.
    """
    parts = _OPAQUE.split(data)
    for index in range(0, len(parts), 2):
        parts[index] = _ENTITY.sub(_numeric_reference, parts[index])
    return b"".join(parts)


def parse_xhtml(data: bytes) -> XhtmlDocument:
    try:
        root = etree.fromstring(_replace_html_entities(data), parser=secure_xml_parser())
    except etree.XMLSyntaxError as exc:
        raise NotWellFormedError(f"not well-formed XML: {exc}") from exc
    docinfo = root.getroottree().docinfo
    return XhtmlDocument(
        root=root,
        xml_declaration=data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<?xml"),
        encoding=docinfo.encoding or "utf-8",
        doctype=docinfo.doctype or None,
    )


def serialize_xhtml(document: XhtmlDocument) -> bytes:
    return etree.tostring(
        document.root.getroottree(),
        xml_declaration=document.xml_declaration,
        encoding=document.encoding,
        doctype=document.doctype,
    )


def local_name(element: etree._Element) -> str:
    """The tag name without its namespace, lowercased; '' for comments and PIs."""
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname.lower()


def text_of(element: etree._Element) -> str:
    """All text inside ``element``: the XML-tree spelling of text_content()."""
    return "".join(element.itertext())


def document_title(root: etree._Element) -> str:
    """The first <h1>'s text, else the <title>'s, else ''.

    Namespace-blind queries such as //h1 find nothing in an XHTML tree, and
    text_content() exists only on lxml.html elements; this works on both.
    """
    for wanted in ("h1", "title"):
        for element in root.iter():
            if local_name(element) == wanted:
                text = " ".join(text_of(element).split())
                if text:
                    return text
    return ""


def parse_fragment(markup: str) -> tuple[str, list[etree._Element]]:
    """Parse translated markup into XHTML-namespace nodes: (leading text, elements).

    A model's reply is meant to be XHTML; when it is not well-formed (an
    unclosed <br>, a bare &), it is parsed as HTML and its elements moved into
    the XHTML namespace, so they never land in the book in no namespace.
    """
    wrapped = f'<wrapper xmlns="{XHTML_NS}" xmlns:epub="{OPS_NS}">{markup}</wrapper>'.encode()
    try:
        wrapper = etree.fromstring(_replace_html_entities(wrapped), parser=secure_xml_parser())
    except etree.XMLSyntaxError:
        from lxml import html as lxml_html

        wrapper = lxml_html.fragment_fromstring(markup, create_parent="wrapper")
        for element in wrapper.iter():
            if isinstance(element.tag, str) and not element.tag.startswith("{"):
                element.tag = f"{{{XHTML_NS}}}{element.tag}"
    return wrapper.text or "", list(wrapper)
