"""A .env file in the working directory supplies API keys, as the README says.

It used to be merged into the settings payload, where OPENAI_API_KEY is not a
field and was silently dropped, so a user who followed the installation guide
got "No OpenAI API key" with the key sitting in .env.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import load_settings
from state.models import ExtractMode, Segment, SegmentMetadata
from translation.providers import create_provider
from translation.providers import openai as openai_module


def _segment() -> Segment:
    return Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello.",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


@pytest.mark.parametrize(
    "name", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPL_API_KEY", "GROK_API_KEY"]
)
def test_dotenv_keys_reach_the_environment(name: str) -> None:
    Path(".env").write_text(f"{name}=from-dotenv\n", encoding="utf-8")
    load_settings()
    assert os.environ[name] == "from-dotenv"


def test_the_openai_provider_sends_the_dotenv_key(monkeypatch: pytest.MonkeyPatch) -> None:
    Path(".env").write_text('OPENAI_API_KEY="sk-from-dotenv"\n', encoding="utf-8")
    settings = load_settings()
    sent: dict = {}

    def fake_post_json(label, url, *, headers=None, **kwargs):
        sent.update(headers or {})
        return {"output": [{"content": [{"type": "output_text", "text": "Bonjour."}]}]}

    monkeypatch.setattr(openai_module, "post_json", fake_post_json)
    provider = create_provider(settings.primary_provider)
    assert provider.translate(_segment(), "en", "fr") == "Bonjour."
    assert sent["Authorization"] == "Bearer sk-from-dotenv"


def test_an_exported_variable_wins_over_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    Path(".env").write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
    load_settings()
    assert os.environ["OPENAI_API_KEY"] == "from-shell"


def test_dotenv_still_carries_ordinary_settings() -> None:
    Path(".env").write_text("target_language=Spanish\n", encoding="utf-8")
    assert load_settings().target_language == "Spanish"
    assert "target_language" not in os.environ
