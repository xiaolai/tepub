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


def _is_external_url(value: str) -> bool:
    return value.startswith(("data:", "http:", "https:", "//", "#"))


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


#: Attributes that can carry a URL and therefore an executable scheme.
_URL_ATTRS = ("href", "src", "action", "formaction", "{http://www.w3.org/1999/xlink}href")


def _sanitise_urls(doc: html.HtmlElement) -> None:
    """Strip URL attributes carrying an executable scheme.

    This runs unconditionally. Link handling previously lived only in
    _rewrite_links, which clean_html calls just when a relative_path is supplied,
    so a javascript: URL survived untouched on the default path.
    """
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in _URL_ATTRS:
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
                    for candidate in value.split(","):
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
UNSAFE_TAGS = ("script", "iframe", "object", "embed", "base", "noscript")


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
