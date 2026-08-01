from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from config import ProviderConfig
from state.models import Segment
from translation.prompt_builder import build_prompt

from .base import BaseProvider, ProviderError, ProviderFatalError, ensure_translation_available


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

        attempts = 3
        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            try:
                response = requests.post(
                    self.config.base_url,
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=60,
                )
            except requests.exceptions.RequestException as exc:
                if last_attempt:
                    raise ProviderError(
                        f"OpenAI request failed after {attempts} attempts: {exc}"
                    ) from exc
                time.sleep(2**attempt)
                continue

            # 429 and 5xx are transient. Treating every status >= 400 as fatal meant
            # a single rate-limit response aborted the whole run without retrying.
            if response.status_code == 429 or response.status_code >= 500:
                if last_attempt:
                    raise ProviderError(
                        f"OpenAI API error {response.status_code} after {attempts} "
                        f"attempts: {response.text}"
                    )
                time.sleep(2**attempt)
                continue

            if response.status_code >= 400:
                # 4xx other than 429 (bad key, unknown model) will not improve on
                # retry and should stop the run.
                raise ProviderFatalError(
                    f"OpenAI API error {response.status_code}: {response.text}"
                )

            try:
                body: Any = response.json()
            except ValueError as exc:
                # Decoding sat outside the guarded block, so a malformed 200
                # response escaped as a raw JSONDecodeError.
                raise ProviderError(f"OpenAI returned a non-JSON response: {exc}") from exc

            return ensure_translation_available(_extract_text(body))

        # Unreachable: every path above either returns or raises.
        raise ProviderError("OpenAI provider failed without an exception")
