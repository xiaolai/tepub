"""A book's glossary: the file, which terms a unit contains, and whether a
translation used their approved renderings."""

from __future__ import annotations

from pathlib import Path

import pytest

from glossary import Glossary, GlossaryError, Term, load_glossary

TERMS = Glossary(
    target_language="Simplified Chinese",
    terms=[
        Term(source="compound", target="诈骗园区", avoid=["集中营"]),
        Term(source="Sihanoukville", target="西哈努克港"),
        Term(source="ProPublica", target="ProPublica"),
        Term(source="pig butchering", target="杀猪盘", variants=["pig-butchering"]),
    ],
)


def _sources(text: str) -> list[str]:
    return [term.source for term in TERMS.terms_in(text)]


def test_terms_are_found_with_plurals_and_any_initial_case() -> None:
    assert _sources("Compounds near Sihanoukville, and one compound.") == [
        "compound",
        "Sihanoukville",
    ]


def test_matching_is_by_whole_word() -> None:
    assert _sources("compounding interest and compoundsome") == []


def test_a_note_number_after_the_word_does_not_hide_it() -> None:
    assert _sources("the compound<sup>12</sup> and Sihanoukville3") == ["compound", "Sihanoukville"]


def test_names_are_case_sensitive() -> None:
    assert _sources("the propublica report") == []


def test_listed_variants_match() -> None:
    assert _sources("a pig-butchering scam") == ["pig butchering"]


def test_terms_are_found_in_html_text_not_attributes() -> None:
    assert _sources('<a href="#compound">see</a> the <em>compound</em>') == ["compound"]


def test_a_translation_missing_a_rendering_is_reported() -> None:
    terms = TERMS.terms_in("ProPublica visited a compound.")
    assert TERMS.problems(terms, "ProPublica 参观了一个诈骗园区。") == []
    assert TERMS.problems(terms, "ProPublica 参观了一个集中营。") == [
        '"compound" must be 诈骗园区, not 集中营',
    ]
    assert TERMS.problems(terms, "该网站参观了一个诈骗 园区。") == [
        '"ProPublica" must stay ProPublica',
    ]


def test_spacing_added_by_polishing_does_not_hide_a_rendering() -> None:
    terms = [Term(source="58 Tongcheng", target="58同城")]
    assert Glossary(target_language="zh", terms=terms).problems(terms, "在 58 同城上") == []


def test_the_file_round_trips_and_terms_without_a_target_are_undecided(tmp_path: Path) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text(
        "target_language: Simplified Chinese\n"
        "terms:\n"
        "  - source: compound\n"
        "    target: 诈骗园区\n"
        "  - source: Along\n",
        encoding="utf-8",
    )
    glossary = load_glossary(path)
    assert [t.source for t in glossary.decided()] == ["compound"]
    assert [t.source for t in glossary.terms_in("Along the compound")] == ["compound"]


@pytest.mark.parametrize(
    ("body", "complaint"),
    [
        ("terms:\n  - source: x\n    target: y\n", "target_language"),
        ("target_language: zh\nterms:\n  - source: x\n  - source: X\n", "twice"),
        ("target_language: zh\nterms:\n  - target: y\n", "source"),
        ("target_language: zh\nterm:\n  - source: x\n", "term"),
    ],
)
def test_a_malformed_file_is_refused_with_its_name(tmp_path: Path, body: str, complaint: str) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(GlossaryError, match=complaint) as error:
        load_glossary(path)
    assert str(path) in str(error.value)


def test_a_book_glossary_overrides_the_global_one() -> None:
    general = Glossary(target_language="zh-CN", terms=[Term(source="compound", target="大院")])
    merged = general.merged_with(TERMS)
    assert {t.source: t.target for t in merged.terms}["compound"] == "诈骗园区"
    assert len(merged.terms) == len(TERMS.terms)
