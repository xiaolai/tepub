from __future__ import annotations

import os
from typing import Any

# Optional import: delay until used to avoid hard dependency in test env
try:  # pragma: no cover - imported lazily
    from google import genai
except ImportError:  # pragma: no cover
    genai = None

from config import ProviderConfig
from state.models import Segment
from translation import prompt_builder

from .base import BaseProvider, ProviderFatalError, ensure_translation_available


class GeminiProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    def _client_instance(self) -> genai.Client:
        if self._client is None:
            if genai is None:
                raise ProviderFatalError("google-genai package not installed")
            api_key = self.config.api_key or os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ProviderFatalError("GEMINI_API_KEY missing; cannot call Gemini provider")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def translate(
        self,
        segment: Segment,
        *,
        source_language: str,
        target_language: str,
    ) -> str:
        client = self._client_instance()

        prompt = prompt_builder.build_prompt(segment, source_language, target_language)
        try:
            response = client.models.generate_content(
                model=self.config.model,
                contents=[{"role": "user", "parts": [prompt]}],
            )
        except Exception as exc:  # pragma: no cover - network dependent
            raise ProviderFatalError(f"Gemini request failed: {exc}") from exc

        text: str | None = None
        if hasattr(response, "text"):
            text = response.text
        elif hasattr(response, "candidates"):
            candidates: Any = response.candidates  # type: ignore[attr-defined]
            if candidates:
                parts = getattr(candidates[0], "content", None)
                if parts and getattr(parts, "parts", None):
                    text = parts.parts[0].text  # type: ignore[attr-defined]
        return ensure_translation_available(text)
