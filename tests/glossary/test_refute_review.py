"""Failure modes found by a refute-mode review of the glossary, each held shut."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from glossary import Glossary, Term


@pytest.mark.parametrize(
    "fields",
    [{"target": " "}, {"target": "银行", "variants": [""]}, {"target": "银行", "avoid": [" "]}],
)
def test_blank_fields_are_refused(fields) -> None:
    with pytest.raises(ValidationError, match="blank"):
        Term(source="bank", **fields)


def test_a_term_inside_a_longer_one_is_not_held_to_its_own_rendering() -> None:
    glossary = Glossary(
        target_language="zh",
        terms=[Term(source="machine learning", target="机器学习"), Term(source="learning", target="学习过程")],
    )
    assert [t.source for t in glossary.terms_in("Machine learning works.")] == ["machine learning"]
    found = glossary.terms_in("Machine learning aids learning.")
    assert sorted(t.source for t in found) == ["learning", "machine learning"]


def test_a_lowercase_term_and_an_acronym_can_coexist() -> None:
    glossary = Glossary(
        target_language="zh", terms=[Term(source="US", target="美国"), Term(source="us", target="我们")]
    )
    assert [t.source for t in glossary.terms_in("The US told us.")] == ["US", "us"]
    assert [t.source for t in glossary.terms_in("THE US")] == ["US"]
    with pytest.raises(ValidationError, match="twice"):
        Glossary(target_language="zh", terms=[Term(source="compound"), Term(source="Compound")])


def test_a_latin_rendering_must_be_a_whole_word() -> None:
    glossary = Glossary(target_language="zh", terms=[Term(source="cat", target="cat")])
    assert glossary.problems(glossary.decided(), "education") == ['"cat" must stay cat']
    assert glossary.problems(glossary.decided(), "the cat sat") == []


def test_an_avoided_word_beside_the_approved_rendering_is_fine() -> None:
    glossary = Glossary(
        target_language="zh", terms=[Term(source="compound", target="诈骗园区", avoid=["集中营"])]
    )
    assert glossary.problems(glossary.decided(), "诈骗园区不是集中营。") == []
    assert glossary.problems(glossary.decided(), "这是集中营。") == ['"compound" must be 诈骗园区, not 集中营']


@pytest.mark.parametrize(
    "html",
    ["a scam<br/>compound", 'a scam<a href="#n">1</a> compound', "a scam<sup>2</sup> compound"],
)
def test_line_breaks_and_note_references_do_not_hide_a_phrase(html) -> None:
    glossary = Glossary(target_language="zh", terms=[Term(source="scam compound", target="诈骗园区")])
    assert [t.source for t in glossary.terms_in(html)] == ["scam compound"]


@pytest.mark.parametrize(
    ("term", "text", "matches"),
    [
        ("rat", "rates", False),
        ("rate", "rates", True),
        ("box", "boxes", True),
        ("party", "parties", True),
        ("art", "art-work", False),
        ("art", "pop-art show", False),  # a hyphenated compound is one word
        ("art", "the art show", True),
    ],
)
def test_plural_and_hyphen_rules(term, text, matches) -> None:
    assert bool(Term(source=term).pattern.search(text)) is matches
