from __future__ import annotations

import asyncio
from functools import lru_cache

import edge_tts


@lru_cache(maxsize=1)
def _all_edge_voices() -> list[dict]:
    return asyncio.run(edge_tts.list_voices())


def list_edge_voices_for_language(language_code: str | None) -> list[dict]:
    """List Edge TTS voices for a specific language.

    Args:
        language_code: Language code prefix (e.g., "en", "zh")

    Returns:
        List of voice dictionaries with metadata
    """
    voices = _all_edge_voices()
    if not language_code:
        return voices
    prefix = language_code.lower()
    filtered = [voice for voice in voices if voice.get("Locale", "").lower().startswith(prefix)]
    if filtered:
        return filtered
    return voices


def list_openai_voices() -> list[dict]:
    """List OpenAI TTS voices.

    Returns:
        List of voice dictionaries with metadata
    """
    return [
        {
            "ShortName": "alloy",
            "Locale": "en-US",
            "Gender": "Neutral",
            "Description": "Neutral, balanced voice suitable for most content",
        },
        {
            "ShortName": "echo",
            "Locale": "en-US",
            "Gender": "Male",
            "Description": "Male voice, authoritative and clear",
        },
        {
            "ShortName": "fable",
            "Locale": "en-GB",
            "Gender": "Male",
            "Description": "British accent, expressive, great for storytelling",
        },
        {
            "ShortName": "onyx",
            "Locale": "en-US",
            "Gender": "Male",
            "Description": "Deep male voice, serious and professional",
        },
        {
            "ShortName": "nova",
            "Locale": "en-US",
            "Gender": "Female",
            "Description": "Female voice, energetic and friendly",
        },
        {
            "ShortName": "shimmer",
            "Locale": "en-US",
            "Gender": "Female",
            "Description": "Female voice, warm and expressive",
        },
    ]


def list_voices_for_provider(
    provider: str,
    language_code: str | None = None
) -> list[dict]:
    """List voices for a specific TTS provider.

    Args:
        provider: "edge" or "openai"
        language_code: Optional language filter (Edge TTS only)

    Returns:
        List of voice dictionaries

    Raises:
        ValueError: If provider is unknown
    """
    provider = provider.lower()

    if provider == "edge":
        return list_edge_voices_for_language(language_code)
    elif provider == "openai":
        return list_openai_voices()
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


# Backward compatibility
def list_voices_for_language(language_code: str | None) -> list[dict]:
    """Legacy function for Edge TTS voice listing.

    Deprecated: Use list_voices_for_provider("edge", language_code) instead.
    """
    return list_edge_voices_for_language(language_code)


def format_voice_entry(voice: dict, provider: str = "edge") -> str:
    """Format a voice entry for display.

    Args:
        voice: Voice dictionary with metadata
        provider: TTS provider ("edge" or "openai")

    Returns:
        Formatted string for display
    """
    locale = voice.get("Locale", "")
    short_name = voice.get("ShortName", "")
    gender = voice.get("Gender", "")

    if provider == "openai":
        description = voice.get("Description", "")
        return f"{short_name} ({locale}, {gender}) - {description}"
    else:  # edge
        style_list = voice.get("StyleList") or []
        styles = ", ".join(style_list) if style_list else "-"
        return f"{short_name} ({locale}, {gender}, styles: {styles})"
