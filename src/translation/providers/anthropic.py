from __future__ import annotations

import os

# Optional import to avoid hard dependency during tests
try:  # pragma: no cover
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

from config import ProviderConfig
from state.models import Segment
from translation import prompt_builder

from .base import BaseProvider, ProviderError, ProviderFatalError, ensure_translation_available


class AnthropicProvider(BaseProvider):
    supports_html = True

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        api_key = self.config.api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderFatalError("ANTHROPIC_API_KEY missing; cannot call Anthropic provider")
        if anthropic is None:
            raise ProviderFatalError("anthropic package not installed")
        self._client = anthropic.Anthropic(api_key=api_key)

    def translate(
        self,
        segment: Segment,
        *,
        source_language: str,
        target_language: str,
    ) -> str:
        prompt = prompt_builder.build_prompt(segment, source_language, target_language)

        try:
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system="You are a precise literary translator.",
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:  # pragma: no cover - network dependent
            raise ProviderFatalError(f"Anthropic request failed: {exc}") from exc

        if not response.content:
            raise ProviderFatalError("Anthropic response missing content")

        # A truncated response used to be stored as a complete translation, losing
        # the tail of any segment longer than the limit with no indication.
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise ProviderError(
                f"Translation for segment {segment.segment_id} was truncated at "
                f"{self.config.max_tokens} tokens. Raise `max_tokens` for this "
                f"provider in config.yaml."
            )

        text = response.content[0].text
        return ensure_translation_available(text)
