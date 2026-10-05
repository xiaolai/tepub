"""Candidate terms for a book's glossary: its index headings and its names.

An index is the author's own list of what matters, so its headings come
first. Names, runs of capitalised words that recur, cover books without one.
Every candidate is counted in the book's text with the same matching the
glossary uses, and one that never occurs there is dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from lxml import etree

from epub_io.xhtml import local_name

from .model import Term

# Where a heading ends: a page number or a cross-reference, after a comma or a
# space; a number inside the heading ("14K triad") does not end it.
_HEADING_END = re.compile(r"(?:,\s*|\s+)(?:\d+(?=\s*(?:[,–-]|$))|[Ss]ee\b|passim\b)")
# Text before a word that starts a sentence: a full stop, perhaps followed by a
# closing quote or a note number ("compounds.12 The"), or a line start.
_SENTENCE_START = re.compile(r"(?:^|[.!?:;][\"”’)\]\d\s]*|[\"“‘(]|\n)\s*$")
_TRAILING = re.compile(r"(?:['’]s?|-)+$")
# Capitalised everywhere in English, yet never glossary terms.
_NEVER_TERMS = frozenset(
    "January February March April May June July August September October November "
    "December Monday Tuesday Wednesday Thursday Friday Saturday Sunday I Mr Mrs Ms Dr "
    "Chapter Part Introduction Conclusion Preface Foreword Epilogue Prologue Notes Index "
    "Acknowledgements Acknowledgments Contents Figure Table Appendix".split()
)
_DEMONYM_ENDINGS = ("ese", "ian", "ish", "an", "i", "e")


def _nationality_words(names: list[str]) -> set[str]:
    """Words naming a nationality or language, derived from a place in the list.

    Their rendering depends on the sentence (Chinese: 中国, 中文, 华人), so
    holding them to one would be wrong. Cambodian derives from Cambodia, Thai
    from Thailand, Philippine from Philippines, Chinese from China.
    """
    singles = [name for name in names if " " not in name]
    derived = set()
    for word in singles:
        if not word.endswith(_DEMONYM_ENDINGS):
            continue
        for place in singles:
            if place == word:
                continue
            if (
                word.startswith(place)
                or (place.startswith(word) and place[len(word):] in ("land", "s", "es"))
                or (word[:4] == place[:4] and word.endswith(("ese", "ian", "ish")))
            ):
                derived.add(word)
                break
    return derived
_QUALIFIER = re.compile(r"\s*\([^)]*\)")
_INVERTED_NAME = re.compile(r"^([A-Z][\w'’.-]+(?: [A-Z][\w'’.-]+)?), ([A-Z][\w'’.-]+(?: [A-Z][\w'’.]+)*)$")
_NAME = re.compile(r"\b[A-Z][a-zA-Z'’-]+(?:\s+(?:of\s+|de\s+|van\s+|von\s+|al-)?[A-Z][a-zA-Z'’-]+)*")
_LEADING_ARTICLE = re.compile(r"^(?:The|A|An)\s+")


def _entry_text(entry: etree._Element) -> str:
    """An index entry's own text, without its nested sub-entries."""
    parts = [entry.text or ""]
    for child in entry:
        if isinstance(child.tag, str) and local_name(child) in ("ul", "ol", "dl"):
            break
        parts.append("".join(child.itertext()))
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def index_headings(index: etree._Element) -> list[str]:
    """Headings of an index document's entries, names put in reading order."""
    headings = []
    for entry in index.iter():
        if not isinstance(entry.tag, str) or local_name(entry) not in ("li", "p", "dd", "dt"):
            continue
        text = _entry_text(entry)
        match = _HEADING_END.search(text)
        if match is None:
            continue  # a title or a note, not an entry
        heading = _QUALIFIER.sub("", text[: match.start()]).strip(" ,;:–-")
        name = _INVERTED_NAME.match(heading)
        if name:
            heading = f"{name.group(2)} {name.group(1)}"
        elif "," in heading:
            heading = heading.split(",")[0].strip()  # "animal lovers, scams targeting"
        if 2 <= len(heading) <= 60:
            headings.append(heading)
    return headings


def recurring_names(text: str, *, min_count: int) -> list[str]:
    """Runs of capitalised words that recur inside sentences.

    A single word is a name only when it is capitalised inside a sentence and
    the book does not use it more often in lowercase: "However" opens
    sentences, and "Fraud" in the titles of cited reports is the noun fraud.
    """
    counts: Counter[str] = Counter()
    inside: Counter[str] = Counter()
    for match in _NAME.finditer(text):
        name = _TRAILING.sub("", _LEADING_ARTICLE.sub("", match.group()))
        if len(name) < 2 or name in _NEVER_TERMS:
            continue
        counts[name] += 1
        if not _SENTENCE_START.search(text[max(0, match.start() - 12) : match.start()]):
            inside[name] += 1
    lowered = Counter(re.findall(r"(?<![\w-])[a-z][a-z-]*(?![\w])", text))
    names = []
    for name, count in counts.items():
        if " " in name:
            # A run of capitalised words is a name wherever it stands.
            if count >= min_count:
                names.append(name)
        elif inside[name] >= min_count and lowered[name.lower()] < count:
            names.append(name)
    return names


def _singular(heading: str, text: str) -> str:
    """An index's plural heading as the term that matches both forms.

    The stem must occur on its own in the book: "rates" is "rate", not "rat",
    and "businesses" is "business", not "businesse".
    """
    if heading != heading.lower() or not heading.endswith("s") or heading.endswith("ss"):
        return heading
    for stem in (heading[:-1], heading[:-2]):
        if Term(source=stem).pattern.fullmatch(heading) and re.search(
            rf"(?<![\w-]){re.escape(stem)}(?![\w])", text, re.IGNORECASE
        ):
            return stem
    return heading


def candidates(
    indexes: Iterable[etree._Element],
    text: str,
    *,
    known: set[str],
    min_count: int = 2,
    limit: int = 200,
) -> list[tuple[str, int]]:
    """(term, occurrences in the book), most frequent first, up to ``limit``."""
    proposed: dict[str, None] = {}
    for index in indexes:
        for heading in index_headings(index):
            # A lowercase one-word heading ("police") is a general word; the
            # book's own terms are phrases ("scam compound") or names.
            if heading == heading.lower() and " " not in heading:
                continue
            proposed[_singular(heading, text)] = None
    proposed.update(dict.fromkeys(recurring_names(text, min_count=min_count)))
    # A word that mostly occurs inside a longer candidate ("Democracy" in
    # "Voice of Democracy") is a fragment of it, not a term of its own.
    phrases = [Term(source=source).pattern for source in proposed if " " in source]
    outside_phrases = text
    for pattern in phrases:
        outside_phrases = pattern.sub(" ", outside_phrases)
    counted = []
    seen = {term.casefold() for term in known}
    for source in proposed:
        if source.casefold() in seen:
            continue
        seen.add(source.casefold())
        pattern = Term(source=source).pattern
        count = len(pattern.findall(text))
        if count < min_count:
            continue
        if " " not in source and len(pattern.findall(outside_phrases)) < count / 2:
            continue
        counted.append((source, count))
    nationality = _nationality_words([source for source, _ in counted])
    counted = [item for item in counted if item[0] not in nationality]
    counted.sort(key=lambda item: (-item[1], item[0].casefold()))
    return counted[:limit]
