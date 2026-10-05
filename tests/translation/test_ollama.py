"""Ollama: the think option."""

from __future__ import annotations

import json

import pytest

from config import ProviderConfig
from translation.providers import create_provider
from translation.providers import ollama as ollama_module


def _ollama(**config) -> object:
    return create_provider(ProviderConfig(name="ollama", model="translategemma:12b", **config))


@pytest.mark.parametrize(("think", "expected"), [(None, "absent"), (False, False), (True, True)])
def test_think_is_sent_only_when_set(monkeypatch, think, expected) -> None:
    sent = {}

    def capture(label, url, *, headers, data, timeout):
        sent.update(json.loads(data))
        return {"response": "好", "done_reason": "stop"}

    monkeypatch.setattr(ollama_module, "post_json", capture)
    from tests.translation.test_reply_checks import _segment

    _ollama(think=think).translate(_segment(), "fr", "zh")
    assert sent.get("think", "absent") == expected


def test_think_is_rejected_for_other_providers() -> None:
    with pytest.raises(ValueError, match="Ollama option"):
        ProviderConfig(name="openai", model="gpt-4o", think=False)
