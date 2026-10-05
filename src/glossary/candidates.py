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
# Words that open a sentence and glue onto a name after them: "Then Nguyễn
# Văn Đức spoke" holds the name, not "Then Nguyễn Văn Đức".
_OPENERS = frozenset(
    "A An And As At After Also Although Because But By Even For From He Her Here His "
    "However I If In It Its Later Many Meanwhile Most My Now On Once One Our She Since "
    "So Some Still That The Their Then There These They This Those Though To Today "
    "We When While With Yet Here See Note Please Example Step Section".split()
)
# Words that cannot start or end a term: "types of" and "amygdala and" are
# halves of index sub-entries, not terms.
_FUNCTION_WORDS = frozenset(
    "a an and as at by for from in into of on or the to with".split()
)
_CONTRACTION = re.compile(r"['’](?:m|ve|d|ll|re|t|s)$", re.IGNORECASE)


def _not_a_term(name: str, lowered: Counter[str], capitalised: int) -> bool:
    """A function word, a contraction, a section heading, or a single word the
    book uses more often in lowercase ("Fraud" in a cited title is fraud)."""
    words = name.split()
    if words[0].lower() in _FUNCTION_WORDS or words[-1].lower() in _FUNCTION_WORDS:
        return True
    if len(words) > 1:
        return False
    return (
        name in _OPENERS
        or name in _NEVER_TERMS
        or bool(_CONTRACTION.search(name))
        or lowered[name.lower()] >= capitalised
    )


def _lowercase_words(text: str) -> Counter[str]:
    return Counter(re.findall(r"(?<![\w-])[a-z][a-z-]*(?![\w])", text))


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


def _uppercase_class() -> str:
    """A character class of every uppercase letter in the Basic Multilingual
    Plane, so that names such as José, Nguyễn and Đức are names too."""
    ranges: list[list[int]] = []
    for code in range(0x10000):
        if chr(code).isupper():
            if ranges and ranges[-1][1] == code - 1:
                ranges[-1][1] = code
            else:
                ranges.append([code, code])
    return "".join(
        re.escape(chr(a)) if a == b else f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in ranges
    )


_UPPER = _uppercase_class()
_CAPITALISED = rf"[{_UPPER}][\w'’-]*"
_INVERTED_NAME = re.compile(
    rf"^({_CAPITALISED}(?: {_CAPITALISED})?), ({_CAPITALISED}\.?(?: {_CAPITALISED}\.?)*)$"
)
_NAME = re.compile(
    # Spaces, not line breaks, join the words of a name: units are joined with
    # newlines, and a heading must not run into the next unit's first word.
    rf"(?<![\w'’-]){_CAPITALISED}(?:[^\S\n]+(?:of[^\S\n]+|de[^\S\n]+|van[^\S\n]+|von[^\S\n]+|al-)?{_CAPITALISED})*"
)
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
        at_start = bool(_SENTENCE_START.search(text[max(0, match.start() - 12) : match.start()]))
        first, _, rest = name.partition(" ")
        if rest and first in _OPENERS:
            name, at_start = rest, False  # the name proper starts after the opener
        if len(name) < 2 or name in _NEVER_TERMS:
            continue
        counts[name] += 1
        if not at_start:
            inside[name] += 1
    lowered = _lowercase_words(text)
    names = []
    for name, count in counts.items():
        if count < min_count or _not_a_term(name, lowered, count):
            continue
        # A run of capitalised words is a name wherever it stands; a single
        # word must also appear inside sentences, not only open them.
        if " " in name or inside[name] >= min_count:
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
    lowered = _lowercase_words(text)
    for index in indexes:
        for heading in index_headings(index):
            heading = heading.rstrip(".")
            # A lowercase one-word heading ("police") is a general word; the
            # book's own terms are phrases ("scam compound") or names.
            if not heading or (heading == heading.lower() and " " not in heading):
                continue
            # Counting capitalised uses takes a pass over the book; only a
            # single word needs it.
            capitalised = (
                len(re.findall(rf"(?<![\w-]){re.escape(heading)}(?![\w])", text))
                if " " not in heading
                else 0
            )
            if _not_a_term(heading, lowered, capitalised):
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
