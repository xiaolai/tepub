"""A reply that refuses the task or was cut off is an error, never a translation.

The refusal filter used to run only in the debug purge command, and only the
Anthropic provider checked for truncation, so both kinds of reply were stored
as COMPLETED and exported into the book.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config import ProviderConfig
from state.models import ExtractMode, Segment, SegmentMetadata
from translation.controller import _translate_segment
from translation.providers import ProviderError, create_provider
from translation.providers import grok as grok_module
from translation.providers import ollama as ollama_module
from translation.providers import openai as openai_module


def _segment(text: str = "Il fait beau aujourd'hui, et nous partons.") -> Segment:
    return Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


class FixedReply:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply: str):
        self.reply = reply

    def translate(self, segment, *, source_language, target_language):
        return self.reply


def test_a_refusal_is_an_error() -> None:
    result = _translate_segment(
        _segment(), FixedReply("I can't help with that request."), "fr", "en"
    )
    assert result.translation is None
    assert isinstance(result.error, ProviderError)
    assert "refused" in str(result.error)


def test_a_translation_that_merely_sounds_apologetic_is_kept() -> None:
    result = _translate_segment(
        _segment("Je suis désolé, je ne peux pas venir ce soir."),
        FixedReply("I'm sorry, I can't come tonight."),
        "fr",
        "en",
    )
    assert result.error is None
    assert result.translation == "I'm sorry, I can't come tonight."


def test_a_source_that_reads_like_a_refusal_is_translated_normally() -> None:
    source = "I can't help with that request, she said."
    result = _translate_segment(_segment(source), FixedReply(source), "en", "en")
    assert result.error is None


def _provider(name: str, **extra) -> object:
    return create_provider(ProviderConfig(name=name, model="m", api_key="k", **extra))


@pytest.mark.parametrize(
    "body",
    [
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"content": [{"type": "output_text", "text": "Half a sent"}]}],
        },
        {"choices": [{"finish_reason": "length", "message": {"content": "Half a sent"}}]},
    ],
)
def test_openai_truncation_is_an_error(monkeypatch, body) -> None:
    monkeypatch.setattr(openai_module, "post_json", lambda *a, **k: body)
    with pytest.raises(ProviderError, match="truncated"):
        _provider("openai").translate(_segment(), "fr", "en")


def test_grok_truncation_is_an_error(monkeypatch) -> None:
    body = {"choices": [{"finish_reason": "length", "message": {"content": "Half a sent"}}]}
    monkeypatch.setattr(grok_module, "post_json", lambda *a, **k: body)
    with pytest.raises(ProviderError, match="truncated"):
        _provider("grok").translate(_segment(), source_language="fr", target_language="en")


def test_ollama_truncation_is_an_error(monkeypatch) -> None:
    body = {"response": "Half a sent", "done": True, "done_reason": "length"}
    monkeypatch.setattr(ollama_module, "post_json", lambda *a, **k: body)
    with pytest.raises(ProviderError, match="truncated"):
        _provider("ollama").translate(_segment(), "fr", "en")


def test_gemini_truncation_is_an_error(monkeypatch) -> None:
    response = SimpleNamespace(
        text="Half a sent",
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))],
    )
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: response))
    provider = _provider("gemini")
    monkeypatch.setattr(provider, "_client_instance", lambda: client)
    with pytest.raises(ProviderError, match="truncated"):
        provider.translate(_segment(), source_language="fr", target_language="en")


def test_a_complete_openai_reply_passes(monkeypatch) -> None:
    body = {
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": "Fine."}]}],
    }
    monkeypatch.setattr(openai_module, "post_json", lambda *a, **k: body)
    assert _provider("openai").translate(_segment(), "fr", "en") == "Fine."


def _html_segment() -> Segment:
    return Segment(
        segment_id="h1",
        file_path=Path("c.xhtml"),
        xpath="/x",
        extract_mode=ExtractMode.HTML,
        source_content='Une affirmation<a href="notes.xhtml#n1"><sup>1</sup></a> claire.',
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


class Replies:
    """A provider that handles tags itself, so HTML units reach it as HTML."""

    name = "fake"
    model = "fake-1"
    uses_markers = False

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts_notes: list[str | None] = []

    def translate(self, segment, *, source_language, target_language):
        self.prompts_notes.append(segment.metadata.notes)
        return self.replies.pop(0)


GOOD = 'A clear claim<a href="notes.xhtml#n1"><sup>1</sup></a>.'
DROPPED = "A clear claim."


def test_a_reply_that_keeps_the_markup_is_used() -> None:
    result = _translate_segment(_html_segment(), Replies(GOOD), "fr", "en")
    assert result.error is None and result.translation == GOOD


def test_changed_markup_is_retried_once_with_the_problem_named() -> None:
    provider = Replies(DROPPED, GOOD)
    result = _translate_segment(_html_segment(), provider, "fr", "en")
    assert result.error is None and result.translation == GOOD
    assert provider.prompts_notes[0] is None
    assert "notes.xhtml#n1" in provider.prompts_notes[1]


def test_markup_changed_twice_is_an_error() -> None:
    result = _translate_segment(_html_segment(), Replies(DROPPED, DROPPED), "fr", "en")
    assert result.translation is None
    assert isinstance(result.error, ProviderError) and "markup" in str(result.error)


def test_the_retry_note_reaches_the_prompt() -> None:
    from translation.prompt_builder import build_prompt

    segment = _html_segment()
    noted = segment.model_copy(
        update={"metadata": segment.metadata.model_copy(update={"notes": "keep the link"})}
    )
    assert "keep the link" in build_prompt(noted, "fr", "en")
    assert "keep the link" not in build_prompt(segment, "fr", "en")


class MarkerReplies(Replies):
    """A language model: inline-only HTML reaches it as text with markers."""

    uses_markers = True

    def translate(self, segment, *, source_language, target_language):
        self.sources = getattr(self, "sources", []) + [segment.source_content]
        return super().translate(
            segment, source_language=source_language, target_language=target_language
        )


def test_a_language_model_sees_markers_and_the_tags_come_back() -> None:
    provider = MarkerReplies("A clear claim⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧.")
    result = _translate_segment(_html_segment(), provider, "fr", "en")
    assert provider.sources == ["Une affirmation⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧ claire."]
    assert result.error is None and result.translation == GOOD


def test_a_lost_marker_is_retried_with_markers_named() -> None:
    provider = MarkerReplies("A clear claim.", "A clear claim⟦1⟧⟦2⟧1⟦/2⟧⟦/1⟧.")
    result = _translate_segment(_html_segment(), provider, "fr", "en")
    assert result.error is None and result.translation == GOOD
    assert "marker" in provider.prompts_notes[1]


def test_the_marker_prompt_asks_for_markers_to_be_kept() -> None:
    from translation.prompt_builder import build_prompt

    segment = _html_segment().model_copy(
        update={"source_content": "Une affirmation⟦1⟧ claire.", "extract_mode": ExtractMode.TEXT}
    )
    assert "Keep every marker exactly once" in build_prompt(segment, "fr", "en")
    assert "marker" not in build_prompt(_html_segment(), "fr", "en")
