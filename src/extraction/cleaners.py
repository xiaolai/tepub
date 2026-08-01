from __future__ import annotations

import re


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def normalize_punctuation(text: str) -> str:
    """Normalize punctuation patterns for consistency.

    Handles common typographical variations:
    - Spaced ellipsis (. . . or . . . .) → ...
    - Ensures single space after ellipsis when followed by text

    Args:
        text: Text to normalize

    Returns:
        Text with normalized punctuation
    """
    # Replace spaced dots (. . . or . . . .) with standard ellipsis
    text = re.sub(r"\.\s+\.\s+\.(?:\s+\.)*", "...", text)
    # Ensure exactly one space after ellipsis when followed by non-whitespace
    text = re.sub(r"\.\.\.\s*(?=\S)", "... ", text)
    return text
