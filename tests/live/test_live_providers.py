"""Live tests against a real model. Never part of the default suite or CI.

They run only when TEPUB_LIVE_BASE_URL names an Ollama-compatible server, for
example ``http://localhost:11434``; TEPUB_LIVE_MODEL picks the model. They
check what fakes cannot: that the providers parse a real reply, and that the
truncation signals the code relies on are the ones a real server sends.

    TEPUB_LIVE_BASE_URL=http://host:11434 TEPUB_LIVE_MODEL=translategemma:12b \\
        python -m pytest -m live -q
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from config import ProviderConfig
from state.models import ExtractMode, Segment, SegmentMetadata
from translation.providers import ProviderError, create_provider
from translation.providers import grok as grok_module
from translation.providers import ollama as ollama_module
from translation.refusal_filter import looks_like_refusal

# Read at import: the isolation fixture clears TEPUB_ variables for each test.
BASE_URL = os.environ.get("TEPUB_LIVE_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("TEPUB_LIVE_MODEL", "translategemma:12b")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not BASE_URL, reason="set TEPUB_LIVE_BASE_URL to run live model tests"),
]

CJK = re.compile(r"[一-鿿]")
SOURCE = "The ship sailed at dawn, and the whole crew cheered as the harbour fell away."


def _segment(text: str = SOURCE) -> Segment:
    return Segment(
        segment_id="live-1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


def _limit_output(module, monkeypatch, *, ollama_tokens=None, chat_tokens=None) -> None:
    """Wrap the real HTTP call so the request asks for a tiny reply."""
    real = module.post_json

    def limited(label, url, **kwargs):
        if kwargs.get("data") is not None:
            payload = json.loads(kwargs["data"])
            payload.setdefault("options", {})["num_predict"] = ollama_tokens
            kwargs["data"] = json.dumps(payload)
        if kwargs.get("json_payload") is not None:
            kwargs["json_payload"] = {**kwargs["json_payload"], "max_tokens": chat_tokens}
        return real(label, url, **kwargs)

    monkeypatch.setattr(module, "post_json", limited)


def test_ollama_native_translates() -> None:
    provider = create_provider(
        ProviderConfig(name="ollama", model=MODEL, base_url=f"{BASE_URL}/api/generate")
    )
    reply = provider.translate(_segment(), "en", "Simplified Chinese")
    assert CJK.search(reply), reply
    assert not looks_like_refusal(reply)


def test_openai_compatible_chat_translates() -> None:
    provider = create_provider(
        ProviderConfig(
            name="grok", model=MODEL, api_key="unused", base_url=f"{BASE_URL}/v1/chat/completions"
        )
    )
    reply = provider.translate(_segment(), source_language="en", target_language="Simplified Chinese")
    assert CJK.search(reply), reply


def test_responses_api_translates() -> None:
    provider = create_provider(
        ProviderConfig(name="openai", model=MODEL, api_key="unused", base_url=f"{BASE_URL}/v1/responses")
    )
    reply = provider.translate(_segment(), "en", "Simplified Chinese")
    assert CJK.search(reply), reply


def test_ollama_native_truncation_is_refused(monkeypatch) -> None:
    _limit_output(ollama_module, monkeypatch, ollama_tokens=4)
    provider = create_provider(
        ProviderConfig(name="ollama", model=MODEL, base_url=f"{BASE_URL}/api/generate")
    )
    with pytest.raises(ProviderError, match="truncated"):
        provider.translate(_segment(), "en", "Simplified Chinese")


def test_chat_truncation_is_refused(monkeypatch) -> None:
    _limit_output(grok_module, monkeypatch, chat_tokens=3)
    provider = create_provider(
        ProviderConfig(
            name="grok", model=MODEL, api_key="unused", base_url=f"{BASE_URL}/v1/chat/completions"
        )
    )
    with pytest.raises(ProviderError, match="truncated"):
        provider.translate(_segment(), source_language="en", target_language="Simplified Chinese")
