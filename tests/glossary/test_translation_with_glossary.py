"""The glossary at translation time: in the prompt, checked, retried once."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from glossary import Glossary, GlossaryError, Term, glossary_for
from state.models import ExtractMode, Segment, SegmentMetadata
from translation.controller import _translate_segment
from translation.prompt_builder import build_prompt

GLOSSARY = Glossary(
    target_language="Simplified Chinese",
    terms=[
        Term(source="compound", target="诈骗园区"),
        Term(source="ProPublica", target="ProPublica"),
        Term(source="Along"),  # undecided: never used
    ],
)


def _segment(text: str, mode=ExtractMode.TEXT) -> Segment:
    return Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/x",
        extract_mode=mode,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


class Model:
    name = "fake"
    model = "fake-1"
    uses_markers = True
    follows_instructions = True

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def translate(self, segment, *, source_language, target_language):
        self.prompts.append(build_prompt(segment, source_language, target_language))
        return self.replies.pop(0)


def test_only_the_unit_terms_reach_the_prompt() -> None:
    model = Model("ProPublica 报道了诈骗园区。")
    result = _translate_segment(
        _segment("ProPublica reported on the compounds, Along said."), model, "en", "Simplified Chinese", GLOSSARY
    )
    assert result.error is None
    prompt = model.prompts[0]
    assert '"compound" → 诈骗园区' in prompt
    assert '"ProPublica" → ProPublica (leave as is)' in prompt
    assert "Along" not in prompt.split("SOURCE:")[0]


def test_a_missed_term_is_retried_with_the_term_named() -> None:
    model = Model("他们参观了集中营。", "他们参观了诈骗园区。")
    result = _translate_segment(_segment("They visited a compound."), model, "en", "zh", GLOSSARY)
    assert result.translation == "他们参观了诈骗园区。"
    assert '"compound" must be 诈骗园区' in model.prompts[1]


def test_a_term_missed_twice_is_kept_and_logged(caplog) -> None:
    model = Model("他们参观了那里。", "他们又参观了那里。")
    with caplog.at_level(logging.WARNING):
        result = _translate_segment(_segment("They visited a compound."), model, "en", "zh", GLOSSARY)
    assert result.error is None and result.translation == "他们又参观了那里。"
    assert "Glossary not followed in s1" in caplog.text


def test_a_provider_that_ignores_prompts_is_not_retried() -> None:
    model = Model("他们参观了那里。")
    model.follows_instructions = False
    result = _translate_segment(_segment("They visited a compound."), model, "en", "zh", GLOSSARY)
    assert result.error is None and len(model.prompts) == 1


def test_markup_and_terms_are_named_in_one_retry() -> None:
    source = 'A compound<a href="#n1"><sup>1</sup></a>.'
    model = Model("一个园区。", "一个诈骗园区⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧。")
    result = _translate_segment(_segment(source, ExtractMode.HTML), model, "en", "zh", GLOSSARY)
    assert result.translation == '一个诈骗园区<a href="#n1"><sup>1</sup></a>。'
    note = model.prompts[1]
    assert "changed the markup" in note and '"compound" must be 诈骗园区' in note


def test_terms_are_not_written_to_segments_json() -> None:
    segment = _segment("x")
    hinted = segment.model_copy(
        update={"metadata": segment.metadata.model_copy(update={"terms": {"a": "b"}})}
    )
    assert "terms" not in hinted.model_dump()["metadata"]


def test_the_book_glossary_overrides_the_global_one(tmp_path: Path) -> None:
    root, book = tmp_path / "root", tmp_path / "book"
    root.mkdir(), book.mkdir()
    (root / "glossary.yaml").write_text(
        "target_language: zh-CN\nterms:\n  - source: compound\n    target: 大院\n"
        "  - source: triad\n    target: 三合会\n",
        encoding="utf-8",
    )
    (book / "glossary.yaml").write_text(
        "target_language: Simplified Chinese\nterms:\n  - source: compound\n    target: 诈骗园区\n",
        encoding="utf-8",
    )
    glossary = glossary_for(root, book, "Simplified Chinese")
    assert {t.source: t.target for t in glossary.terms} == {"triad": "三合会", "compound": "诈骗园区"}
    assert glossary_for(root, tmp_path / "none", "zh-CN") is not None
    assert glossary_for(tmp_path / "x", tmp_path / "y", "zh-CN") is None


def test_a_glossary_for_another_language_is_refused(tmp_path: Path) -> None:
    (tmp_path / "glossary.yaml").write_text(
        "target_language: Spanish\nterms:\n  - source: compound\n    target: complejo\n",
        encoding="utf-8",
    )
    with pytest.raises(GlossaryError, match="is for Spanish"):
        glossary_for(tmp_path / "nowhere", tmp_path, "Simplified Chinese")
