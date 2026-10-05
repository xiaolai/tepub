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

from collections import Counter

from lxml import html as lxml_html

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
