from __future__ import annotations

import os
from typing import Any

from config import ProviderConfig
from state.models import ExtractMode, Segment
from translation.languages import describe_language

from .base import BaseProvider, ProviderError, ProviderFatalError, ensure_translation_available
from .http import post_json


class DeepLProvider(BaseProvider):
    supports_html = True
    uses_markers = False
    follows_instructions = False

    DEFAULT_BASE_URL = "https://api.deepl.com/v2/translate"
    FREE_BASE_URL = "https://api-free.deepl.com/v2/translate"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Writing the default back onto self.config mutated the caller's
        # ProviderConfig, so a shared config object silently acquired a base_url
        # it never declared. Resolve it per call instead: the endpoint also
        # follows the key's tier, and an environment key is unknown here.
        self.base_url = self.config.base_url

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
        # HTML segments were posted as plain text, so DeepL translated the markup
        # itself and the tags came back mangled. tag_handling tells DeepL to
        # preserve them, which is what makes supports_html True below.
        if segment.extract_mode == ExtractMode.HTML:
            data["tag_handling"] = "html"
        source = _deepl_source_lang_code(source_language)
        if source and source.lower() != "auto":
            data["source_lang"] = source

        # A free-tier key (":fx") is rejected by the paid endpoint and vice
        # versa; only an explicitly configured base_url overrides the tier.
        endpoint = self.base_url or (
            self.FREE_BASE_URL if api_key.endswith(":fx") else self.DEFAULT_BASE_URL
        )

        # The shared helper retries rate limits and server errors, honouring
        # Retry-After, and classifies failures; DeepL used to treat every one of
        # them, a 429 included, as fatal and end the run.
        payload: Any = post_json("DeepL", endpoint, headers=headers, data=data, timeout=60)

        translations = payload.get("translations") if isinstance(payload, dict) else None
        if translations and isinstance(translations, list):
            translated = translations[0].get("text")
            return ensure_translation_available(translated)
        raise ProviderError("DeepL response missing translation")


#: Codes the DeepL API accepts, which are not all display names. "it", "nl",
#: "pl", "ru" and "en-GB" used to fall through describe_language to None, so a
#: valid target aborted and an explicit source silently became autodetection.
_DEEPL_CODES = frozenset(
    {
        "AR", "BG", "CS", "DA", "DE", "EL", "EN", "EN-GB", "EN-US", "ES", "ET",
        "FI", "FR", "HU", "ID", "IT", "JA", "KO", "LT", "LV", "NB", "NL", "PL",
        "PT", "PT-BR", "PT-PT", "RO", "RU", "SK", "SL", "SV", "TR", "UK", "ZH",
        "ZH-HANS", "ZH-HANT",
    }
)

#: Regional variants are target-only: as a source, DeepL takes EN, PT and ZH.
_SOURCE_VARIANTS = {
    "EN-GB": "EN",
    "EN-US": "EN",
    "PT-BR": "PT",
    "PT-PT": "PT",
    "ZH-HANS": "ZH",
    "ZH-HANT": "ZH",
}


def _deepl_lang_code(language: str) -> str | None:
    code = language.strip().upper().replace("_", "-")
    if code in _DEEPL_CODES:
        return code
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
        "simplified chinese": "ZH-HANS",
        # Was mapped to plain ZH, which DeepL renders as Simplified — a request for
        # Traditional Chinese silently produced the wrong script.
        "traditional chinese": "ZH-HANT",
        "korean": "KO",
    }
    return mapping.get(display)


def _deepl_source_lang_code(language: str) -> str | None:
    code = _deepl_lang_code(language)
    return _SOURCE_VARIANTS.get(code, code) if code else None
