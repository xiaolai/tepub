"""max_tokens reaches every provider that can be told it.

The setting was documented as capping each reply, but only Anthropic sent it;
OpenAI, Grok, Gemini and Ollama replied as long as they liked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from config import ProviderConfig
from translation.providers import create_provider
from translation.providers import grok as grok_module
from translation.providers import ollama as ollama_module
from translation.providers import openai as openai_module

from .test_reply_checks import _segment


def _config(name: str) -> ProviderConfig:
    return ProviderConfig(name=name, model="m", api_key="k", max_tokens=321)


@pytest.mark.parametrize(
    ("name", "module", "reply", "read"),
    [
        (
            "openai",
            openai_module,
            {"status": "completed", "output": [{"content": [{"type": "output_text", "text": "x"}]}]},
            lambda sent: sent["max_output_tokens"],
        ),
        (
            "grok",
            grok_module,
            {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]},
            lambda sent: sent["max_tokens"],
        ),
        (
            "ollama",
            ollama_module,
            {"response": "x", "done_reason": "stop"},
            lambda sent: sent["options"]["num_predict"],
        ),
    ],
)
def test_http_providers_send_the_limit(monkeypatch, name, module, reply, read) -> None:
    sent = {}

    def capture(label, url, **kwargs):
        payload = kwargs.get("json_payload")
        sent.update(payload if payload is not None else json.loads(kwargs["data"]))
        return reply

    monkeypatch.setattr(module, "post_json", capture)
    create_provider(_config(name)).translate(_segment(), source_language="fr", target_language="en")
    assert read(sent) == 321


def test_gemini_sends_the_limit(monkeypatch) -> None:
    calls = {}
    response = SimpleNamespace(
        text="x", candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))]
    )

    def generate_content(**kwargs):
        calls.update(kwargs)
        return response

    provider = create_provider(_config("gemini"))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    monkeypatch.setattr(provider, "_client_instance", lambda: client)
    provider.translate(_segment(), source_language="fr", target_language="en")
    assert calls["config"]["max_output_tokens"] == 321
