"""Proposing glossary entries and writing them for review."""

from __future__ import annotations

from pathlib import Path

from glossary import load_glossary
from glossary.build import clean_rendering, context_of, propose, render_proposals

TEXT = "Intro text here. The scam compound near Sihanoukville was raided by police. Later on."


def test_context_is_the_sentence_where_the_term_first_occurs() -> None:
    assert context_of("Sihanoukville", TEXT) == "The scam compound near Sihanoukville was raided by police."


def test_renderings_are_cleaned_and_sentences_refused() -> None:
    assert clean_rendering("民主之声 (Mínzhǔ zhī Shēng)\n", "Voice of Democracy") == "民主之声"
    assert clean_rendering("“诈骗园区”。", "scam compound") == "诈骗园区"
    assert clean_rendering("这是一个很长的句子，解释了这个词在这本书中的各种含义以及它的历史背景和用法。", "triad") is None
    assert clean_rendering("  \n", "triad") is None


def test_proposals_are_written_as_a_valid_glossary(tmp_path: Path) -> None:
    calls = []

    def translate(term: str, context: str) -> str:
        calls.append(context)
        return {"scam compound": "诈骗园区", "Sihanoukville": "西港"}[term]

    proposals = propose([("scam compound", 2), ("Sihanoukville", 1)], TEXT, translate)
    assert calls[0].startswith("The scam compound")
    path = tmp_path / "glossary.yaml"
    path.write_text(render_proposals(proposals, "Simplified Chinese"), encoding="utf-8")
    glossary = load_glossary(path)
    assert {t.source: t.target for t in glossary.terms} == {"scam compound": "诈骗园区", "Sihanoukville": "西港"}
    assert "# 2x: The scam compound near" in path.read_text(encoding="utf-8")


def test_without_a_model_every_entry_is_left_to_fill_in(tmp_path: Path) -> None:
    proposals = propose([('a "quoted" term: x', 3)], 'A "quoted" term: x here.', None)
    path = tmp_path / "glossary.yaml"
    path.write_text(render_proposals(proposals, "zh"), encoding="utf-8")
    glossary = load_glossary(path)
    assert glossary.terms[0].source == 'a "quoted" term: x' and glossary.decided() == []
