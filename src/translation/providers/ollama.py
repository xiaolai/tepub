from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ensure_not_truncated, ensure_translation_available
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


class OllamaProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.config.base_url = _generate_endpoint(self.config.base_url)

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
