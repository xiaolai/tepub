from __future__ import annotations

import json
import time
from typing import Any

import requests

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ProviderError, ProviderFatalError, ensure_translation_available


class OpenAIProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not self.config.base_url:
            self.config.base_url = "https://api.openai.com/v1/responses"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        if not self.config.api_key:
            raise ProviderFatalError("OPENAI_API_KEY missing; cannot call OpenAI provider")

        payload = {
            "model": self.config.model,
            "input": build_prompt(segment, source_language, target_language),
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.extra_headers)

        last_exception: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    self.config.base_url,
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=60,
                )
                if response.status_code >= 400:
                    raise ProviderFatalError(
                        f"OpenAI API error {response.status_code}: {response.text}"
                    )

                body: Any = response.json()
                text = None
                if isinstance(body, dict):
                    output = body.get("output") or body.get("choices")
                    if output and isinstance(output, list):
                        first = output[0]
                        if isinstance(first, dict):
                            text = first.get("text") or first.get("content")
                            if isinstance(text, list) and text:
                                maybe = text[0]
                                if isinstance(maybe, dict):
                                    text = maybe.get("text")
                return ensure_translation_available(text)
            except requests.exceptions.RequestException as exc:
                last_exception = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                else:
                    raise ProviderFatalError(
                        f"OpenAI request failed after 3 attempts: {exc}"
                    ) from exc

        if last_exception:
            raise ProviderFatalError(f"OpenAI request failed: {last_exception}") from last_exception

        raise ProviderError("OpenAI provider failed without an exception")
