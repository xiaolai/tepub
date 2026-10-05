from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import requests

from config import ProviderConfig
from logging_utils.logger import get_logger
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import (
    BaseProvider,
    ProviderFatalError,
    ensure_not_truncated,
    ensure_translation_available,
)
from .http import post_json

DEFAULT_SERVER = "http://localhost:11434"


def _generate_endpoint(base_url: str | None) -> str:
    """Accept a server address or a full endpoint.

    The docs and OLLAMA_BASE_URL give the server, http://localhost:11434, but the
    provider posted to base_url as written, so requests went to the server root
    and failed. A URL with no path now gets /api/generate appended.
    """
    url = (base_url or DEFAULT_SERVER).rstrip("/")
    if urlsplit(url).path in ("", "/"):
        return f"{url}/api/generate"
    return url


logger = get_logger(__name__)


def _installed(model: str, names: set[str]) -> bool:
    # Ollama lists an untagged model under its :latest tag.
    return model in names or (":" not in model and f"{model}:latest" in names)


class OllamaProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.config.base_url = _generate_endpoint(self.config.base_url)

    def preflight(self) -> None:
        """Check the server answers and has the model, before the first segment.

        Ollama is the default provider, so a machine without it, or without the
        model pulled, is the likeliest first run. Each segment used to fail its
        retries and the run then waited through its cooldowns before giving up.
        """
        server = self.config.base_url.rsplit("/api/", 1)[0]
        try:
            response = requests.get(f"{server}/api/tags", timeout=10)
        except requests.exceptions.RequestException as exc:
            raise ProviderFatalError(
                f"Cannot reach Ollama at {server} ({exc.__class__.__name__}). Start it with "
                "'ollama serve', or set primary_provider in ~/.tepub/config.yaml."
            ) from exc
        try:
            names = {entry["name"] for entry in response.json()["models"]}
        except (ValueError, KeyError, TypeError):
            # Not Ollama's own API (a proxy exposing only /api/generate, say);
            # the first request will tell.
            logger.warning("Could not list the models at %s; skipping the model check", server)
            return
        if not _installed(self.config.model, names):
            raise ProviderFatalError(
                f"Ollama at {server} does not have the model {self.config.model!r}. "
                f"Install it with 'ollama pull {self.config.model}'."
            )

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": build_prompt(segment, source_language, target_language),
            "stream": False,
        }
        if self.config.think is not None:
            payload["think"] = self.config.think
        body: Any = post_json(
            "Ollama",
            self.config.base_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=120,
        )
        if isinstance(body, dict):
            ensure_not_truncated(body.get("done_reason") == "length", "Ollama", segment)
        text = body.get("response") if isinstance(body, dict) else None
        return ensure_translation_available(text)
