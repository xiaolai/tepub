from __future__ import annotations

import json
import os
from typing import Any

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import (
    BaseProvider,
    ProviderFatalError,
    ensure_not_truncated,
    ensure_translation_available,
)
from .http import post_json


def _is_truncated(body: Any) -> bool:
    """The Responses API marks a cut-off reply incomplete; Chat Completions says length."""
    if not isinstance(body, dict):
        return False
    if body.get("status") == "incomplete":
        return True
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason") == "length"
    return False


def _part_texts(parts: Any) -> list[str]:
    """The text of each content part, in order, leaving reasoning parts out."""
    texts = []
    for part in parts:
        if isinstance(part, dict):
            if part.get("type", "output_text") not in ("output_text", "text"):
                continue
            text = part.get("text")
            # A whitespace-only part still joins the parts around it: dropping
            # it turned "First" + " " + "Second" into "FirstSecond".
            if isinstance(text, str):
                texts.append(text)
        elif isinstance(part, str):
            texts.append(part)
    return texts


def _extract_text(body: Any) -> str | None:
    """Pull the translated text out of a Responses or Chat Completions body.

    Only the first text part used to be returned, so a reply the API split into
    several output messages or output_text parts lost everything after the
    first while it was recorded as complete.
    """
    if not isinstance(body, dict):
        return None

    if isinstance(body.get("output"), list):
        items = body["output"]
    elif isinstance(body.get("choices"), list):
        # Each choice is an alternative; the first is the reply.
        items = body["choices"][:1]
    else:
        return None

    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # Chat Completions nests the text under "message".
        message = item.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
                continue
            if isinstance(content, list):
                texts.extend(_part_texts(content))
                continue

        candidate = item.get("text") or item.get("content")
        if isinstance(candidate, str):
            texts.append(candidate)
        elif isinstance(candidate, list):
            texts.extend(_part_texts(candidate))
    return "".join(texts) or None


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
            "max_output_tokens": self.config.max_tokens,
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
        ensure_not_truncated(_is_truncated(body), "OpenAI", segment)
        return ensure_translation_available(_extract_text(body))
