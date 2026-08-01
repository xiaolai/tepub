from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from posixpath import normpath

from lxml import html

MEDIA_ATTRS: list[tuple[str, Sequence[str]]] = [
    ("img", ("src", "srcset")),
    ("source", ("src", "srcset")),
    ("video", ("src", "poster")),
    ("audio", ("src",)),
    ("track", ("src",)),
    ("object", ("data",)),
    ("embed", ("src",)),
    ("image", ("{http://www.w3.org/1999/xlink}href", "xlink:href", "href")),
    ("use", ("{http://www.w3.org/1999/xlink}href", "xlink:href", "href")),
]


#: Schemes that must be left untouched rather than rewritten to a content path.
_EXTERNAL_PREFIXES = (
    "data:",
    "http:",
    "https:",
    "//",
    "#",
    "mailto:",
    "tel:",
    "ftp:",
    "file:",
    "blob:",
)


def _is_external_url(value: str) -> bool:
    # URL schemes are case-insensitive; the check was case-sensitive and covered
    # only four prefixes, so "HTTPS://…" and mailto:/tel: links were treated as
    # relative and rewritten into a broken content/ path.
    return value.strip().lower().startswith(_EXTERNAL_PREFIXES)


def _prefix_content_path(relative_to: Path, url: str) -> str:
    if not url or _is_external_url(url):
        return url
    if url.startswith("content/"):
        return url
    base = relative_to.parent.as_posix()
    combined = f"{base}/{url}" if base else url
    normalised = normpath(combined)
    return f"content/{normalised}"


#: Schemes that execute code when followed. A book is untrusted input, and the
#: web export is opened in a browser, so these must never survive into the output.
_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:text/html")


def _is_dangerous_url(value: str) -> bool:
    # Strip whitespace and control characters, which browsers ignore when parsing
    # a scheme ("java\tscript:alert(1)" still executes).
    collapsed = "".join(ch for ch in value if not ch.isspace() and ord(ch) > 31).lower()
    return collapsed.startswith(_DANGEROUS_SCHEMES)


#: Local attribute names that can carry a URL. Matched by *local* name across
#: every namespace and prefix: checking a fixed list of literal names missed
#: `xlink:href` written as a literal attribute on SVG elements, which lxml keeps
#: verbatim rather than expanding to the namespaced form.
_URL_ATTR_LOCALNAMES = frozenset(
    {
        "href", "src", "srcset", "action", "formaction", "data", "poster",
        "background", "longdesc", "usemap", "cite", "profile", "codebase",
        # SVG animation targets: <animate values="javascript:…"> is live content.
        "values", "from", "to", "by",
    }
)


def _local_name(attr: str) -> str:
    """Attribute name without a namespace URI or prefix."""
    if attr.startswith("{"):
        attr = attr.rsplit("}", 1)[-1]
    if ":" in attr:
        attr = attr.rsplit(":", 1)[-1]
    return attr.lower()


def _sanitise_urls(doc: html.HtmlElement) -> None:
    """Strip attributes carrying an executable scheme, in any namespace.

    Runs unconditionally. Link handling previously lived only in _rewrite_links,
    which clean_html calls just when a relative_path is supplied, so a
    javascript: URL survived untouched on the default path.
    """
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if _local_name(attr) not in _URL_ATTR_LOCALNAMES:
                continue
            value = el.get(attr)
            if value and _is_dangerous_url(value):
                del el.attrib[attr]


def _rewrite_links(doc: html.HtmlElement, relative_path: Path) -> None:
    for el in doc.xpath(".//a[@href]"):
        href = el.get("href")
        if not href:
            continue
        if href.startswith("mailto:"):
            continue
        if href.startswith("#"):
            continue
        fragment = ""
        path_part = href
        if "#" in href:
            path_part, fragment = href.split("#", 1)
        if not path_part:
            continue
        resolved = _prefix_content_path(relative_path, path_part)
        if resolved == path_part:
            continue
        if fragment:
            el.set("href", f"{resolved}#{fragment}")
        else:
            el.set("href", resolved)


def _split_srcset(value: str) -> list[str]:
    """Split a srcset on candidate boundaries, not on every comma.

    A data: URL's payload may itself contain commas, so a plain split(",")
    shredded such entries into fragments. Commas inside a data: URL are only
    separators once whitespace follows them.
    """
    candidates: list[str] = []
    current: list[str] = []
    in_data_url = False
    index = 0
    while index < len(value):
        char = value[index]
        if not in_data_url and "".join(current).strip().lower().endswith("data:") is False:
            # Detect entry into a data: URL by looking at the token so far.
            token = "".join(current).lstrip()
            if token.lower().startswith("data:"):
                in_data_url = True
        if char == "," and (not in_data_url or value[index + 1 : index + 2].isspace()):
            candidates.append("".join(current))
            current = []
            in_data_url = False
        else:
            current.append(char)
        index += 1
    if current:
        candidates.append("".join(current))
    return candidates


def _rewrite_media_urls(doc: html.HtmlElement, relative_path: Path) -> None:
    for tag, attrs in MEDIA_ATTRS:
        for el in doc.xpath(f".//{tag}"):
            for attr in attrs:
                value = el.get(attr)
                if value is None and attr.startswith("{"):
                    _, local = attr.rsplit("}", 1)
                    value = el.get(local)
                if value is None and ":" in attr:
                    _, local = attr.rsplit(":", 1)
                    value = el.get(local)
                if value is None:
                    value = el.attrib.get(attr)
                if not value:
                    continue
                if attr == "srcset":
                    parts: list[str] = []
                    for candidate in _split_srcset(value):
                        candidate = candidate.strip()
                        if not candidate:
                            continue
                        if " " in candidate:
                            url_part, descriptor = candidate.split(" ", 1)
                            parts.append(
                                f"{_prefix_content_path(relative_path, url_part)} {descriptor.strip()}"
                            )
                        else:
                            parts.append(_prefix_content_path(relative_path, candidate))
                    if parts:
                        el.set(attr, ", ".join(parts))
                else:
                    el.set(attr, _prefix_content_path(relative_path, value))


REMOVABLE_TAGS = {"font", "center"}
REMOVABLE_ATTRS = {"style", "class", "lang", "xml:lang"}

#: Elements dropped with their contents. The book is untrusted input and the
#: export is opened in a browser, but none of these were removed, so a book
#: containing <script>…</script> produced a page that executed it.
UNSAFE_TAGS = (
    "script", "iframe", "object", "embed", "base", "noscript",
    "frame", "frameset", "applet", "meta", "link", "template", "portal",
    # SVG/MathML foreign content can execute without a <script> element:
    # <animate attributeName="href" values="javascript:…"> is live.
    "animate", "animatemotion", "animatetransform", "set", "handler",
    "foreignobject",
)


def _remove_tags(doc: html.HtmlElement) -> None:
    # Whole subtree, not drop_tag: drop_tag keeps the element's text, which for a
    # <script> would leave the source code inline in the page.
    for tag in UNSAFE_TAGS:
        for el in doc.xpath(f".//{tag}"):
            parent = el.getparent()
            if parent is not None:
                el.drop_tree()

    for tag in REMOVABLE_TAGS:
        for el in doc.xpath(f".//{tag}"):
            el.drop_tag()


def _strip_attributes(doc: html.HtmlElement) -> None:
    # `.//*` excludes the root element, so attributes on it survived stripping.
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue

        # Inline event handlers execute on load or interaction; none were removed.
        for name in [a for a in el.attrib if a.lower().startswith("on")]:
            del el.attrib[name]

        for attr in REMOVABLE_ATTRS:
            if attr not in el.attrib:
                continue
            if attr in ("class", "style") and el.get("data-lang"):
                # Preserve class/style on translation/original nodes if present
                del el.attrib[attr]
                continue
            el.attrib.pop(attr, None)


def _normalise_images(doc: html.HtmlElement) -> None:
    for img in doc.xpath(".//img"):
        if "loading" not in img.attrib:
            img.attrib["loading"] = "lazy"
        if "decoding" not in img.attrib:
            img.attrib["decoding"] = "async"
        # Ensure images don't overflow
        classes = [cls for cls in img.attrib.get("class", "").split() if cls]
        if "tepub-img" not in classes:
            classes.append("tepub-img")
        if classes:
            img.attrib["class"] = " ".join(classes)
        else:
            img.attrib.pop("class", None)


def clean_html(content: bytes | str, *, relative_path: Path | None = None) -> str:
    parser = html.HTMLParser(encoding="utf-8")
    doc = html.fromstring(content, parser=parser)
    _remove_tags(doc)
    _sanitise_urls(doc)
    _strip_attributes(doc)
    _normalise_images(doc)
    if relative_path is not None:
        _rewrite_media_urls(doc, relative_path)
        _rewrite_links(doc, relative_path)
    return html.tostring(doc, encoding="unicode", method="html")


def ensure_parseable(content: str) -> None:
    # Raises if not well-formed
    html.fromstring(content)
