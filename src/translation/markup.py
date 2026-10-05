"""The markup contract for translated HTML units (decision D6).

A translation of an HTML unit must keep the same tags and the same href, src,
id and epub:type values, in any order: another language may move a link to
follow its own word order. Ruby annotation is exempt, because languages
annotate differently, and so are emphasis-style tags: measured on TranslateGemma
from English into Chinese, every reply that still failed after a retry had only
dropped or added an <em>, and an untranslated paragraph is worse than lost
emphasis. Without this check a reply that dropped a
footnote reference or renamed an anchor was injected as it stood.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass

from lxml import etree
from lxml import html as lxml_html

from epub_io.container import secure_xml_parser
from epub_io.xhtml import OPS_NS, XHTML_NS, local_name

_RUBY = frozenset({"ruby", "rt", "rp", "rb", "rtc"})
_EMPHASIS = frozenset({"em", "i", "b", "strong", "u", "small", "mark", "cite", "q", "s"})
_EXEMPT = _RUBY | _EMPHASIS
_KEPT_VALUES = ("href", "src", "id", "epub:type")


def _inventory(markup: str) -> tuple[Counter, Counter]:
    root = lxml_html.fragment_fromstring(markup or "", create_parent="tepub-fragment")
    tags: Counter = Counter()
    values: Counter = Counter()
    for node in root.iterdescendants():
        if not isinstance(node.tag, str):
            continue
        name = node.tag.lower()
        if name in _EXEMPT:
            continue
        tags[name] += 1
        for attribute in _KEPT_VALUES:
            value = node.get(attribute)
            if value is not None:
                values[(attribute, value)] += 1
    return tags, values


def _describe(counter: Counter, *, kind: str) -> str:
    parts = []
    for item, count in sorted(counter.items(), key=str):
        label = f"<{item}>" if kind == "tag" else f'{item[0]}="{item[1]}"'
        parts.append(label if count == 1 else f"{label} x{count}")
    return ", ".join(parts)


def markup_mismatch(source: str, translated: str) -> str | None:
    """None when the markup matches; otherwise a short description for the model."""
    source_tags, source_values = _inventory(source)
    translated_tags, translated_values = _inventory(translated)
    problems = []
    if missing := source_tags - translated_tags:
        problems.append("missing " + _describe(missing, kind="tag"))
    if extra := translated_tags - source_tags:
        problems.append("added " + _describe(extra, kind="tag"))
    if missing := source_values - translated_values:
        problems.append("missing or changed " + _describe(missing, kind="value"))
    if extra := translated_values - source_values:
        problems.append("unexpected " + _describe(extra, kind="value"))
    return "; ".join(problems) or None


# Markers: an element with content travels as a pair, ⟦n⟧ … ⟦/n⟧, and an empty
# one (an anchor, an image) as a single ⟦n⟧. Asked to keep raw HTML,
# TranslateGemma moved or dropped anchors and note links in about a quarter of
# the paragraphs of a link-dense book; a model never sees the tags it carries
# back this way, so it cannot mangle their attributes.
_MARKER = re.compile(r"⟦\s*(/?)\s*(\d+)\s*⟧")
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_PREFIXES = {OPS_NS: "epub", _XML_NS: "xml"}
# Structure a reader would lose if a marker went astray; such units keep the
# HTML route and its check.
_NOT_INLINE = frozenset(
    {
        "address", "article", "aside", "blockquote", "caption", "dd", "details", "div",
        "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "li", "math", "nav", "ol", "p", "pre", "script", "section", "style",
        "svg", "table", "tbody", "td", "template", "tfoot", "th", "thead", "tr", "ul",
    }
)


@dataclass(frozen=True)
class Tag:
    """One protected element: its start tag, and whether it wrapped content."""

    name: str
    start: str
    paired: bool


def has_markers(text: str) -> bool:
    return _MARKER.search(text) is not None


def _start_tag(element: etree._Element) -> str | None:
    parts = [local_name(element)]
    for key, value in element.attrib.items():
        name = etree.QName(key)
        if name.namespace is None:
            attribute = name.localname
        elif name.namespace in _PREFIXES:
            attribute = f"{_PREFIXES[name.namespace]}:{name.localname}"
        else:
            return None
        parts.append(f'{attribute}="{html.escape(value)}"')
    return "<" + " ".join(parts)


def protect(markup: str) -> tuple[str, list[Tag]] | None:
    """Plain text with numbered markers, and the tags they stand for.

    None when the fragment cannot travel this way: it holds block structure,
    a foreign namespace, or text that already looks like a marker.
    """
    if "⟦" in markup or "⟧" in markup:
        return None
    wrapped = f'<w xmlns="{XHTML_NS}" xmlns:epub="{OPS_NS}">{markup}</w>'.encode("utf-8")
    try:
        root = etree.fromstring(wrapped, parser=secure_xml_parser())
    except etree.XMLSyntaxError:
        return None
    tags: list[Tag] = []
    parts: list[str] = [root.text or ""]

    def walk(element: etree._Element) -> bool:
        if not isinstance(element.tag, str):  # comments and processing instructions
            parts.append(element.tail or "")
            return True
        if etree.QName(element).namespace != XHTML_NS or local_name(element) in _NOT_INLINE:
            return False
        start = _start_tag(element)
        if start is None:
            return False
        number = len(tags) + 1
        paired = bool(element.text) or len(element) > 0
        tags.append(Tag(local_name(element), start, paired))
        parts.append(f"⟦{number}⟧")
        if paired:
            parts.append(element.text or "")
            if not all(walk(child) for child in element):
                return False
            parts.append(f"⟦/{number}⟧")
        parts.append(element.tail or "")
        return True

    if not all(walk(child) for child in root):
        return None
    return "".join(parts), tags


def _kept_markers(tokens: list[tuple[bool, int]], tags: list[Tag]) -> set[int]:
    """Positions of the markers to turn back into tags.

    A pair is kept when its first opening marker is closed later, properly
    nested; a single when it first appears. Anything else the model wrote,
    a broken, repeated or invented marker, is dropped.
    """
    stack: list[tuple[int, int]] = []  # (tag number, position of its opening marker)
    seen: set[int] = set()
    kept: set[int] = set()
    for position, (closing, number) in enumerate(tokens):
        if not 1 <= number <= len(tags):
            continue
        if not tags[number - 1].paired:
            if not closing and number not in seen:
                seen.add(number)
                kept.add(position)
        elif not closing:
            if number not in seen:
                seen.add(number)
                stack.append((number, position))
        elif any(open_number == number for open_number, _ in stack):
            while True:
                open_number, opened_at = stack.pop()
                if open_number == number:
                    kept.update((opened_at, position))
                    break
    return kept


def restore(reply: str, tags: list[Tag]) -> str:
    """The reply as markup: markers become the original tags, text is escaped.

    A marker the model invented, repeated or left unpaired is dropped and its
    words kept; the markup check then reports what went missing.
    """
    pieces = _MARKER.split(reply)
    # split yields text, then (slash, number, text) for each marker.
    tokens = [(pieces[i] == "/", int(pieces[i + 1])) for i in range(1, len(pieces), 3)]
    kept = _kept_markers(tokens, tags)
    out = [html.escape(pieces[0], quote=False)]
    for position, ((closing, number), text) in enumerate(zip(tokens, pieces[3::3])):
        if position in kept:
            tag = tags[number - 1]
            if not tag.paired:
                out.append(tag.start + "/>")
            else:
                out.append(f"</{tag.name}>" if closing else tag.start + ">")
        out.append(html.escape(text, quote=False))
    return "".join(out)
