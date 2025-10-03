from __future__ import annotations

_LANGUAGE_MAP = {
    "auto": ("auto", "Auto"),
    "automatic": ("auto", "Auto"),
    "detect": ("auto", "Auto"),
    "en": ("en", "English"),
    "eng": ("en", "English"),
    "english": ("en", "English"),
    "zh": ("zh", "Chinese"),
    "cn": ("zh-CN", "Simplified Chinese"),
    "zh-cn": ("zh-CN", "Simplified Chinese"),
    "zh_cn": ("zh-CN", "Simplified Chinese"),
    "simplified": ("zh-CN", "Simplified Chinese"),
    "simplified chinese": ("zh-CN", "Simplified Chinese"),
    "zh-hant": ("zh-TW", "Traditional Chinese"),
    "zh_tw": ("zh-TW", "Traditional Chinese"),
    "traditional": ("zh-TW", "Traditional Chinese"),
    "traditional chinese": ("zh-TW", "Traditional Chinese"),
    "es": ("es", "Spanish"),
    "spanish": ("es", "Spanish"),
    "fr": ("fr", "French"),
    "french": ("fr", "French"),
    "de": ("de", "German"),
    "german": ("de", "German"),
    "ja": ("ja", "Japanese"),
    "japanese": ("ja", "Japanese"),
    "ko": ("ko", "Korean"),
    "korean": ("ko", "Korean"),
}


def normalize_language(value: str) -> tuple[str, str]:
    key = value.strip().lower()
    if not key:
        return ("auto", "Auto")
    if key in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[key]
    # Fall back to treating raw value as display name
    return (value.strip(), value.strip())


def describe_language(code: str) -> str:
    for stored_code, display in _LANGUAGE_MAP.values():
        if stored_code == code:
            return display
    return code
