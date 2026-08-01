from __future__ import annotations

import json
import os
from typing import Any

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ProviderFatalError, ensure_translation_available
from .http import post_json


def _extract_text(body: Any) -> str | None:
    """Pull the translated text out of a Responses or Chat Completions body.

    Only ``output[0]`` used to be examined, but the Responses API may emit a
    reasoning item before the message, in which case the translation was reported
    as missing even though the call succeeded. Every item is scanned instead.
    """
    if not isinstance(body, dict):
        return None

    items = body.get("output") or body.get("choices")
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue

        # Chat Completions nests the text under "message".
        message = item.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]

        candidate = item.get("text") or item.get("content")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        if isinstance(candidate, list):
            for part in candidate:
                if isinstance(part, dict):
                    part_text = part.get("text")
                    if isinstance(part_text, str) and part_text.strip():
                        return part_text
                elif isinstance(part, str) and part.strip():
                    return part
    return None


class OpenAIProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not self.config.base_url:
            self.config.base_url = "https://api.openai.com/v1/responses"

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            # The environment variable was named in the error but never actually
            # consulted, so exporting it did not help.
            raise ProviderFatalError(
                "No OpenAI API key: set `api_key` for this provider in config.yaml "
                "or export OPENAI_API_KEY."
            )

        payload = {
            "model": self.config.model,
            "input": build_prompt(segment, source_language, target_language),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.extra_headers)

        body: Any = post_json(
            "OpenAI",
            self.config.base_url,
            headers=headers,
            data=json.dumps(payload),
            timeout=60,
        )
        return ensure_translation_available(_extract_text(body))
