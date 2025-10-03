from __future__ import annotations

import os
from typing import Any

import requests

from config import ProviderConfig
from state.models import Segment
from translation.languages import describe_language

from .base import BaseProvider, ProviderFatalError, ensure_translation_available


class DeepLProvider(BaseProvider):
    supports_html = False

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not self.config.base_url:
            self.config.base_url = "https://api.deepl.com/v2/translate"

    def translate(
        self,
        segment: Segment,
        *,
        source_language: str,
        target_language: str,
    ) -> str:
        api_key = self.config.api_key or os.getenv("DEEPL_API_KEY")
        if not api_key:
            raise ProviderFatalError("DEEPL_API_KEY missing; cannot call DeepL provider")

        headers = {
            "Authorization": f"DeepL-Auth-Key {api_key}",
        }

        target = _deepl_lang_code(target_language)
        if not target:
            raise ProviderFatalError(f"Unsupported target language for DeepL: {target_language}")

        data = {
            "text": segment.source_content,
            "target_lang": target,
        }
        source = _deepl_lang_code(source_language)
        if source and source.lower() != "auto":
            data["source_lang"] = source

        try:
            response = requests.post(
                self.config.base_url,
                headers=headers,
                data=data,
                timeout=60,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network dependent
            raise ProviderFatalError(f"DeepL request failed: {exc}") from exc

        payload: Any = response.json()
        translations = payload.get("translations") if isinstance(payload, dict) else None
        if translations and isinstance(translations, list):
            translated = translations[0].get("text")
            return ensure_translation_available(translated)
        raise ProviderFatalError("DeepL response missing translation")


def _deepl_lang_code(language: str) -> str | None:
    display = describe_language(language).lower()
    mapping = {
        "german": "DE",
        "english": "EN",
        "british english": "EN-GB",
        "american english": "EN-US",
        "spanish": "ES",
        "french": "FR",
        "italian": "IT",
        "dutch": "NL",
        "polish": "PL",
        "portuguese": "PT-PT",
        "brazilian portuguese": "PT-BR",
        "russian": "RU",
        "japanese": "JA",
        "chinese": "ZH",
        "simplified chinese": "ZH",
        "traditional chinese": "ZH",  # DeepL returns simplified
        "korean": "KO",
    }
    return mapping.get(display)
