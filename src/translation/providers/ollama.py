from __future__ import annotations

import json
from typing import Any

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ensure_translation_available
from .http import post_json


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
        body: Any = post_json(
            "Ollama",
            self.config.base_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=120,
        )
        text = body.get("response") if isinstance(body, dict) else None
        return ensure_translation_available(text)
