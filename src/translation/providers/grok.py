from __future__ import annotations

import os
from typing import Any

from config import ProviderConfig
from state.models import Segment
from translation import prompt_builder

from .base import BaseProvider, ProviderFatalError, ensure_translation_available
from .http import post_json


class GrokProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not self.config.base_url:
            self.config.base_url = "https://api.x.ai/v1/chat/completions"

    def translate(
        self,
        segment: Segment,
        *,
        source_language: str,
        target_language: str,
    ) -> str:
        api_key = self.config.api_key or os.getenv("GROK_API_KEY")
        if not api_key:
            raise ProviderFatalError("GROK_API_KEY missing; cannot call Grok provider")

        prompt = prompt_builder.build_prompt(segment, source_language, target_language)

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "You are a precise literary translator."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.extra_headers)

        data: Any = post_json(
            "Grok",
            self.config.base_url,
            headers=headers,
            json_payload=payload,
            timeout=120,
        )

        output = None
        if isinstance(data, dict):
            choices = data.get("choices")
            if choices and isinstance(choices, list):
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        output = message.get("content")
        if isinstance(output, list) and output:
            # Some implementations return a list of dicts with text
            maybe = output[0]
            if isinstance(maybe, dict):
                output = maybe.get("text")
        return ensure_translation_available(output)
