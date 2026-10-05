"""A glossary: approved renderings of a book's terms, and the checks on them.

An entry without a target is undecided and is not used. Lowercase terms match
in any initial case and with an English plural ending; terms with a capital,
names mostly, match exactly. Matching is by whole word, on a unit's text, never
inside its markup.
"""

from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path

import yaml
from lxml import html as lxml_html
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from translation.languages import normalize_language

GLOSSARY_FILE = "glossary.yaml"


class GlossaryError(ValueError):
    """A glossary file that cannot be used as written."""


class Term(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str | None = None
    variants: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    note: str | None = None
    # How often the term occurs in the book; written by `glossary build` to
    # help review, never read.
    count: int | None = None

    @field_validator("source", "target")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        # A blank target would pass every reply, a blank variant match every
        # paragraph, and a blank avoid fail every reply.
        if value is not None and not value.strip():
            raise ValueError("must not be blank; leave the target out for an undecided term")
        return value.strip() if value is not None else None

    @field_validator("variants", "avoid")
    @classmethod
    def _no_blank_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("entries must not be blank")
        return tuple(value.strip() for value in values)

    @cached_property
    def pattern(self) -> re.Pattern[str]:
        return re.compile("|".join(_form_pattern(form) for form in (self.source, *self.variants)))

    @property
    def kept(self) -> bool:
        """Left as in the source, as names of publications often are."""
        return self.target == self.source


def _spaced(char: str) -> bool:
    """Whether the character belongs to a script that separates words with spaces."""
    return char.isalnum() and not (
        "\u2e80" <= char <= "\u9fff" or "\uac00" <= char <= "\ud7af" or "\uf900" <= char <= "\ufaff"
    )


def _plural(word: str) -> str:
    """The word with an optional English plural ending: rate(s), box(es), part(y|ies)."""
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return f"{re.escape(word)}(?:es)?"
    if len(word) > 2 and word.endswith("y") and word[-2] not in "aeiou":
        return f"{re.escape(word[:-1])}(?:y|ies)"
    return f"{re.escape(word)}s?"


def _form_pattern(form: str) -> str:
    words = form.split()
    if form == form.lower() and form[-1:].isalpha():
        # A lowercase term: each word lowercase or capitalised (a sentence or a
        # title may start with it), never all capitals ("us" is not "US"), and
        # an English plural.
        parts = [re.escape(word) for word in words[:-1]] + [_plural(words[-1])]
        parts = [
            f"[{part[0]}{part[0].upper()}]{part[1:]}" if part[:1].isalpha() else part
            for part in parts
        ]
    else:
        parts = [re.escape(word) for word in words]
    body = r"\s+".join(parts)
    # Word boundaries only where the term starts or ends in a script with
    # spaces. Only a letter continues a word: a note number follows its word
    # directly ("compound1") and must not hide it; a hyphen and a letter do
    # ("art-work" is not "art").
    start = r"(?<![^\W\d_])(?<![^\W\d_]-)" if _spaced(form[:1]) else ""
    end = r"(?![^\W\d_])(?!-[^\W\d_])" if _spaced(form[-1:]) else ""
    return f"{start}{body}{end}"


_NOTE_REFERENCE = re.compile(r"^[\W\d_]*$")


def plain_text(content: str) -> str:
    """A unit's text, as a reader sees its words.

    Markup goes; a line break reads as a space ("scam<br/>compound"), and note
    references, a <sup> or a link whose text is only a number, are dropped, so
    that "scam<a>1</a> compound" still holds the phrase. Inline code is
    dropped too: it is not translated, so no term in it can be held to one.
    """
    if "<" not in content:
        return content
    root = lxml_html.fragment_fromstring(content, create_parent="div")
    for element in list(root.iter()):
        if element is root or not isinstance(element.tag, str):
            continue
        tag = element.tag.split("}")[-1].lower()
        if tag == "br":
            element.tail = " " + (element.tail or "")
        elif tag in ("sup", "code", "kbd", "samp") or (
            tag == "a" and _NOTE_REFERENCE.match(element.text_content())
        ):
            # Note references, and code, which is not translated.
            element.drop_tree()  # keeps its tail
    return root.text_content()


def _contains(text: str, compact: str, rendering: str) -> bool:
    if _spaced(rendering[:1]) and _spaced(rendering[-1:]):
        words = r"\s+".join(re.escape(word) for word in rendering.split())
        return re.search(rf"(?<![^\W\d_]){words}(?![^\W\d_])", text) is not None
    return _compact(rendering) in compact


def _compact(text: str) -> str:
    # Polishing adds spaces between Chinese and Latin text, so 58同城 can
    # come back as 58 同城; renderings are compared without whitespace.
    return "".join(text.split())


class Glossary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_language: str
    terms: tuple[Term, ...] = Field(default=())

    @model_validator(mode="after")
    def _each_term_once(self) -> Glossary:
        """No two entries for the same words: "compound" also matches
        "Compound", so both cannot be listed; "us" does not match "US"."""
        for index, term in enumerate(self.terms):
            for other in self.terms[index + 1 :]:
                if term.source == other.source or term.pattern.fullmatch(other.source) or other.pattern.fullmatch(term.source):
                    raise ValueError(f"the term {other.source!r} is listed twice")
        return self

    @property
    def language_code(self) -> str:
        return normalize_language(self.target_language)[0]

    def decided(self) -> list[Term]:
        return [term for term in self.terms if term.target]

    def terms_in(self, content: str) -> list[Term]:
        """The decided terms in a unit, each where a longer term does not cover it.

        "learning" inside "machine learning" belongs to the longer term; holding
        it to its own rendering too would demand two renderings of one phrase.
        """
        text = plain_text(content)
        spans = {
            term.source: [match.span() for match in term.pattern.finditer(text)]
            for term in self.decided()
        }
        found = []
        for term in self.decided():
            covered_by_longer = [
                span
                for other, other_spans in spans.items()
                if other != term.source
                for span in other_spans
            ]
            if any(
                not any(a <= start and end <= b and b - a > end - start for a, b in covered_by_longer)
                for start, end in spans[term.source]
            ):
                found.append(term)
        return found

    def problems(self, terms: list[Term], translation: str) -> list[str]:
        """What the translation got wrong about these terms; empty when nothing.

        A rendering in Latin letters must appear as a whole word ("cat" is not in
        "education"); one in Chinese or Japanese anywhere. A rendering to avoid
        counts only when the approved one is missing: "诈骗园区不是集中营" may
        rightly name both.
        """
        text = plain_text(translation)
        compact = _compact(text)
        found = []
        for term in terms:
            if _contains(text, compact, term.target or ""):
                continue
            avoided = [word for word in term.avoid if _contains(text, compact, word)]
            if term.kept:
                found.append(f'"{term.source}" must stay {term.target}')
            elif avoided:
                found.append(f'"{term.source}" must be {term.target}, not {", ".join(avoided)}')
            else:
                found.append(f'"{term.source}" must be {term.target}')
        return found

    def merged_with(self, book: Glossary) -> Glossary:
        """This glossary with the book's terms added; the book's win."""
        if self.language_code != book.language_code:
            raise GlossaryError(
                f"Glossaries for different languages: {self.target_language} and "
                f"{book.target_language}"
            )
        overridden = {term.source.casefold() for term in book.terms}
        kept = [term for term in self.terms if term.source.casefold() not in overridden]
        return Glossary(target_language=book.target_language, terms=(*kept, *book.terms))


def load_glossary(path: Path) -> Glossary:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Glossary.model_validate(data)
    except (yaml.YAMLError, ValidationError, ValueError) as exc:
        raise GlossaryError(f"Cannot use the glossary {path}: {exc}") from exc


def glossary_for(work_root: Path, work_dir: Path, target_language: str) -> Glossary | None:
    """The glossary for a run: ~/.tepub/glossary.yaml, overridden by the book's.

    A glossary written for another target language is refused rather than
    applied: its renderings would be forced into the wrong language.
    """
    wanted = normalize_language(target_language)
    merged: Glossary | None = None
    for path in dict.fromkeys((work_root / GLOSSARY_FILE, work_dir / GLOSSARY_FILE)):
        if not path.exists():
            continue
        glossary = load_glossary(path)
        if glossary.language_code != wanted[0]:
            raise GlossaryError(
                f"The glossary {path} is for {glossary.target_language}, but this run "
                f"translates into {wanted[1]}. Change its target_language or move it aside."
            )
        merged = glossary if merged is None else merged.merged_with(glossary)
    return merged
