"""Settings that do nothing are gone, and a config that names one is told so.

retry, rate_limit and fallback_provider were parsed into the settings model and
read nowhere, while config.example.yaml promised that TEPUB "automatically
switches" to the fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_settings
from config.models import AppSettings


@pytest.mark.parametrize("name", ["retry", "rate_limit", "fallback_provider"])
def test_the_setting_no_longer_exists(name: str) -> None:
    assert name not in AppSettings.model_fields


def test_unknown_keys_in_a_config_are_named(caplog) -> None:
    Path("config.yaml").write_text(
        "target_language: Spanish\nretry:\n  max_attempts: 5\nfallback_provider:\n  name: ollama\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        settings = load_settings()
    assert settings.target_language == "Spanish"
    assert "fallback_provider" in caplog.text and "retry" in caplog.text


def test_the_example_config_names_only_real_settings() -> None:
    import yaml

    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    keys = set(yaml.safe_load(example.read_text(encoding="utf-8")))
    assert keys <= set(AppSettings.model_fields), keys - set(AppSettings.model_fields)


@pytest.mark.parametrize(
    ("given", "posted_to"),
    [
        (None, "http://localhost:11434/api/generate"),
        ("http://localhost:11434", "http://localhost:11434/api/generate"),
        ("http://box:11434/", "http://box:11434/api/generate"),
        ("http://box:11434/api/generate", "http://box:11434/api/generate"),
    ],
)
def test_ollama_accepts_a_server_address_or_an_endpoint(given, posted_to) -> None:
    from config import ProviderConfig
    from translation.providers import create_provider

    provider = create_provider(ProviderConfig(name="ollama", model="m", base_url=given))
    assert provider.config.base_url == posted_to
