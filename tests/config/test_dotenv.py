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
    config = Path.home() / ".tepub" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("primary_provider:\n  name: openai\n  model: gpt-4o\n", encoding="utf-8")
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


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "PYTHONPATH"])
def test_dotenv_cannot_set_other_variables(name: str, monkeypatch, caplog) -> None:
    """A .env in the working directory may name only the variables tepub reads.

    Exporting every entry let a .env in whatever folder tepub was run from route
    API traffic through a proxy or inject a library into ffmpeg.
    """
    monkeypatch.delenv(name, raising=False)
    Path(".env").write_text(f"{name}=/tmp/evil\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        load_settings()
    assert name not in os.environ
    assert name in caplog.text


def test_the_exportable_list_covers_every_variable_the_source_reads() -> None:
    import re

    from config.loader import DOTENV_EXPORTABLE

    src = Path(__file__).resolve().parents[2] / "src"
    read = set()
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        read |= set(re.findall(r'os\.(?:getenv|environ\.get)\("([A-Z0-9_]+)"', text))
    # TEPUB_ variables configure tepub itself and are not secrets a .env should carry.
    provider_vars = {name for name in read if not name.startswith("TEPUB_")}
    assert provider_vars == set(DOTENV_EXPORTABLE)
