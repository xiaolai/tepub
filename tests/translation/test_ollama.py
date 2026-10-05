"""Ollama, the default provider: the preflight check and the think option."""

from __future__ import annotations

import json
from types import SimpleNamespace

import click
import pytest
import requests

from cli.errors import handle_run_errors
from config import AppSettings, ProviderConfig
from translation.providers import ProviderFatalError, create_provider
from translation.providers import ollama as ollama_module


def _ollama(**config) -> object:
    return create_provider(ProviderConfig(name="ollama", model="translategemma:12b", **config))


def _tags(monkeypatch, *names: str) -> None:
    body = {"models": [{"name": name} for name in names]}
    monkeypatch.setattr(
        ollama_module.requests, "get", lambda url, timeout: SimpleNamespace(json=lambda: body)
    )


def test_the_default_provider_is_local() -> None:
    provider = AppSettings().primary_provider
    assert (provider.name, provider.model) == ("ollama", "translategemma:12b")
    assert _ollama().config.base_url == "http://localhost:11434/api/generate"


def test_an_unreachable_server_stops_the_run_with_the_fix(monkeypatch) -> None:
    def refuse(url, timeout):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(ollama_module.requests, "get", refuse)
    with pytest.raises(ProviderFatalError, match="ollama serve"):
        _ollama().preflight()


def test_a_missing_model_stops_the_run_with_the_fix(monkeypatch) -> None:
    _tags(monkeypatch, "qwen3.5:9b")
    with pytest.raises(ProviderFatalError, match="ollama pull translategemma:12b"):
        _ollama().preflight()


def test_an_installed_model_passes(monkeypatch) -> None:
    _tags(monkeypatch, "qwen3.5:9b", "translategemma:12b")
    _ollama().preflight()


def test_an_untagged_model_means_latest(monkeypatch) -> None:
    _tags(monkeypatch, "gemma3:latest")
    create_provider(ProviderConfig(name="ollama", model="gemma3")).preflight()


def test_a_server_that_cannot_list_models_is_not_blocked(monkeypatch) -> None:
    def not_json():
        raise ValueError("no json")

    monkeypatch.setattr(
        ollama_module.requests, "get", lambda url, timeout: SimpleNamespace(json=not_json)
    )
    _ollama(base_url="http://proxy.example/ollama/api/generate").preflight()


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


def test_a_fatal_provider_error_exits_with_code_1() -> None:
    @handle_run_errors
    def command():
        raise ProviderFatalError("Cannot reach Ollama")

    with pytest.raises(click.exceptions.Exit) as exit_info:
        command()
    assert exit_info.value.exit_code == 1
