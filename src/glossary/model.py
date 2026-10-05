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

    @field_validator("source")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source must not be blank")
        return value.strip()

    @cached_property
    def pattern(self) -> re.Pattern[str]:
        return re.compile("|".join(_form_pattern(form) for form in (self.source, *self.variants)))

    @property
    def kept(self) -> bool:
        """Left as in the source, as names of publications often are."""
        return self.target == self.source


def _form_pattern(form: str) -> str:
    body = r"\s+".join(re.escape(word) for word in form.split())
    if form == form.lower() and form[-1:].isalpha():
        # A lowercase term: any initial case, and its plural.
        body = f"(?i:{body}(?:e?s)?)"
    # Word boundaries only where the term starts or ends with a Latin letter or
    # digit; a term in a script written without spaces has none. Only a letter
    # continues a word: a note number follows its word directly in a unit's
    # text ("compound1"), and must not hide the term.
    start = r"(?<![^\W\d_])(?<!-)" if form[:1].isascii() and form[:1].isalnum() else ""
    end = r"(?![^\W\d_])" if form[-1:].isascii() and form[-1:].isalnum() else ""
    return f"{start}{body}{end}"


def plain_text(content: str) -> str:
    """A unit's text: its markup removed when it has any."""
    if "<" not in content:
        return content
    return lxml_html.fragment_fromstring(content, create_parent="div").text_content()


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
        seen: set[str] = set()
        for term in self.terms:
            key = term.source.casefold()
            if key in seen:
                raise ValueError(f"the term {term.source!r} is listed twice")
            seen.add(key)
        return self

    @property
    def language_code(self) -> str:
        return normalize_language(self.target_language)[0]

    def decided(self) -> list[Term]:
        return [term for term in self.terms if term.target]

    def terms_in(self, content: str) -> list[Term]:
        text = plain_text(content)
        return [term for term in self.decided() if term.pattern.search(text)]

    def problems(self, terms: list[Term], translation: str) -> list[str]:
        """What the translation got wrong about these terms; empty when nothing."""
        text = _compact(plain_text(translation))
        found = []
        for term in terms:
            avoided = [word for word in term.avoid if _compact(word) in text]
            if _compact(term.target or "") in text and not avoided:
                continue
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
