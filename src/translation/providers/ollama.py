from __future__ import annotations

import json
from typing import Any

import requests

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ProviderError, ensure_translation_available


class OllamaProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not self.config.base_url:
            self.config.base_url = "http://localhost:11434/api/generate"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        payload = {
            "model": self.config.model,
            "prompt": build_prompt(segment, source_language, target_language),
            "stream": False,
        }
        try:
            response = requests.post(
                self.config.base_url,
                data=json.dumps(payload),
                timeout=120,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.RequestException as exc:
            # Network failures were unhandled and escaped as raw requests
            # exceptions, bypassing the controller's provider-error accounting.
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(f"Ollama error {response.status_code}: {response.text}")

        try:
            body: Any = response.json()
        except ValueError as exc:
            raise ProviderError(f"Ollama returned a non-JSON response: {exc}") from exc
        text = body.get("response") if isinstance(body, dict) else None
        return ensure_translation_available(text)
