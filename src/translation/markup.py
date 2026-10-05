"""The markup contract for translated HTML units (decision D6).

A translation of an HTML unit must keep the same tags and the same href, src,
id and epub:type values, in any order: another language may move a link to
follow its own word order. Ruby annotation is exempt, because languages
annotate differently, and so are emphasis-style tags: measured on TranslateGemma
from English into Chinese, every reply that still failed after a retry had only
dropped or added an <em>, and an untranslated paragraph is worse than lost
emphasis. Line breaks are exempt for the same reason: they carry no link,
anchor or note, and when the translation reorders a phrase there may be no
place left for one. Without this check a reply that dropped a
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
_EXEMPT = _RUBY | _EMPHASIS | {"br"}
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


_STRUCTURE = frozenset({"ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr"})


def _loose_text(markup: str) -> int:
    """Text standing directly in a list or table, outside its items and cells."""
    root = lxml_html.fragment_fromstring(markup or "", create_parent="tepub-fragment")
    count = 0
    # A list unit's source is the inside of its list: items at the top.
    items_at_top = any(
        isinstance(child.tag, str) and child.tag.lower() in ("li", "tr", "dt", "dd", "tbody", "thead")
        for child in root
    )
    for node in root.iter():
        if (node is root and items_at_top) or (
            isinstance(node.tag, str) and node.tag.lower() in _STRUCTURE
        ):
            count += bool((node.text or "").strip())
            count += sum(bool((child.tail or "").strip()) for child in node)
    return count


def markup_mismatch(source: str, translated: str) -> str | None:
    """None when the markup matches; otherwise a short description for the model."""
    source_tags, source_values = _inventory(source)
    translated_tags, translated_values = _inventory(translated)
    problems = []
    # A reply fenced as a code block put "```html" inside a list: invalid,
    # and shown to readers.
    if _loose_text(translated) > _loose_text(source):
        problems.append("text outside the list items or table cells")
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
# back this way, so it cannot mangle their attributes. A line break travels as
# a newline: the same model dropped a lone marker standing for <br/> on every
# title-page line it saw, and keeps newlines.
_MARKER = re.compile(r"⟦\s*(/?)\s*(\d+)\s*⟧")
_MARKERS_AT_END = re.compile(r"(?:⟦\s*/?\s*\d+\s*⟧|\s)*$")
_MARKERS_AT_START = re.compile(r"^(?:⟦\s*/?\s*\d+\s*⟧|\s)*")
_NEWLINE = re.compile(r"[ \t]*\n[ \t]*")
_WHITESPACE = re.compile(r"\s+")
# Scripts written without spaces between words, and their punctuation.
_UNSPACED = re.compile(r"[\u2e80-\u303f\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")
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
    # An empty element that only marks a place for links to land on, such as
    # <a id="p12"/>: nothing shows, so where it sits within the paragraph does
    # not matter to a reader.
    anchor: bool = False
    # The text a pair wrapped when it held text alone, such as a note number;
    # empty otherwise.
    inner: str = ""


@dataclass(frozen=True)
class Markers:
    """What a protected unit's markers stand for."""

    tags: tuple[Tag, ...]
    line_breaks: bool


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


def protect(markup: str) -> tuple[str, Markers] | None:
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
    # Source whitespace is insignificant; only a <br/> becomes a newline.
    parts: list[str] = [_WHITESPACE.sub(" ", root.text or "")]
    line_breaks = False

    def walk(element: etree._Element) -> bool:
        nonlocal line_breaks
        tail = _WHITESPACE.sub(" ", element.tail or "")
        if not isinstance(element.tag, str):  # comments and processing instructions
            parts.append(tail)
            return True
        if etree.QName(element).namespace != XHTML_NS or local_name(element) in _NOT_INLINE:
            return False
        if local_name(element) == "br" and not element.attrib:
            line_breaks = True
            parts.append("\n")
            parts.append(tail)
            return True
        start = _start_tag(element)
        if start is None:
            return False
        number = len(tags) + 1
        paired = bool(element.text) or len(element) > 0
        anchor = (
            not paired
            and local_name(element) in ("a", "span")
            and element.get("id") is not None
            and element.get("href") is None
        )
        inner = (element.text or "").strip() if paired and len(element) == 0 else ""
        tags.append(Tag(local_name(element), start, paired, anchor, inner))
        parts.append(f"⟦{number}⟧")
        if paired:
            parts.append(_WHITESPACE.sub(" ", element.text or ""))
            if not all(walk(child) for child in element):
                return False
            parts.append(f"⟦/{number}⟧")
        parts.append(tail)
        return True

    if not all(walk(child) for child in root):
        return None
    text = _NEWLINE.sub("\n", "".join(parts)).strip(" ")
    return text, Markers(tuple(tags), line_breaks)


def _kept_markers(tokens: list[tuple[bool, int]], tags: tuple[Tag, ...]) -> set[int]:
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


def _join_lines(reply: str) -> str:
    """Join lines a model broke where the source had no line break: with no
    space when either side is Chinese or Japanese, with one otherwise."""

    def join(match: re.Match) -> str:
        before = _MARKERS_AT_END.sub("", reply[: match.start()])[-1:]
        after = _MARKERS_AT_START.sub("", reply[match.end() :])[:1]
        return "" if _UNSPACED.match(before) or _UNSPACED.match(after) else " "

    return re.sub(r"\s*\n\s*", join, reply)


def _close_unclosed(reply: str, tags: tuple[Tag, ...]) -> str:
    """Add a closing marker the model left out, where there is no doubt where.

    Given glossary terms in its prompt, TranslateGemma wrote a note's opening
    marker and its number, "⟦1⟧31 …", but no ⟦/1⟧, in about one note in six.
    When the pair wrapped plain text and the reply has exactly that text right
    after the opening marker, the pair closes after it.
    """
    for number, tag in enumerate(tags, start=1):
        if not tag.inner or re.search(rf"⟦\s*/\s*{number}\s*⟧", reply):
            continue
        opening = re.search(rf"⟦\s*{number}\s*⟧\s*", reply)
        if opening and reply.startswith(tag.inner, opening.end()):
            cut = opening.end() + len(tag.inner)
            reply = f"{reply[:cut]}⟦/{number}⟧{reply[cut:]}"
    return reply


def restore(reply: str, markers: Markers) -> str:
    """The reply as markup: markers become the original tags, text is escaped.

    A marker the model invented, repeated or left unpaired is dropped and its
    words kept; the markup check then reports what went missing. An anchor
    the model dropped is put back at the start.
    """
    tags = markers.tags
    reply = reply.strip()
    if not markers.line_breaks:
        reply = _join_lines(reply)
    reply = _close_unclosed(reply, tags)
    pieces = _MARKER.split(reply)
    # split yields text, then (slash, number, text) for each marker.
    tokens = [(pieces[i] == "/", int(pieces[i + 1])) for i in range(1, len(pieces), 3)]
    kept = _kept_markers(tokens, tags)

    def text(piece: str) -> str:
        return _NEWLINE.sub("<br/>", html.escape(piece, quote=False))

    # An anchor the model dropped goes back at the start: a link to it still
    # lands on this paragraph. TranslateGemma dropped one in about 3% of a
    # book's paragraphs, which then failed the markup check twice and stayed
    # untranslated over a mark no reader sees.
    placed = {tokens[position][1] for position in kept}
    lost_anchors = [
        tag.start + "/>"
        for number, tag in enumerate(tags, start=1)
        if tag.anchor and number not in placed
    ]
    out = [*lost_anchors, text(pieces[0])]
    for position, ((closing, number), piece) in enumerate(zip(tokens, pieces[3::3])):
        if position in kept:
            tag = tags[number - 1]
            if not tag.paired:
                out.append(tag.start + "/>")
            else:
                out.append(f"</{tag.name}>" if closing else tag.start + ">")
        out.append(text(piece))
    return "".join(out)


_FENCE = re.compile(r"^\s*```[\w-]*[ \t]*\n?(.*?)\n?[ \t]*```\s*$", re.DOTALL)
_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^<>]*>|&lt;/?([a-zA-Z][a-zA-Z0-9]*)\b.*?&gt;")


def strip_code_fence(reply: str) -> str:
    """The reply without a Markdown code fence around it, as models sometimes
    return a translation: "```html\n...\n```"."""
    match = _FENCE.match(reply)
    return match.group(1).strip() if match else reply


def stray_tags(source: str, reply: str) -> str | None:
    """Tags in a reply to plain text that the source does not have.

    Sent text or text with markers, a model sometimes answers in HTML, "<p>"
    and all, which then reached the page as literal text. Tags the source
    itself shows, as a book about HTML does, are left alone.
    """
    allowed = {name.lower() for pair in _TAG.findall(source) for name in pair if name}
    stray = sorted(
        {name.lower() for pair in _TAG.findall(reply) for name in pair if name} - allowed
    )
    if not stray:
        return None
    return "added HTML tags " + ", ".join(f"<{name}>" for name in stray) + " to plain text"
