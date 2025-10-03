from __future__ import annotations

import re

from state.models import Segment

_PUNCT_ONLY = re.compile(r"^[\s…—–―·•\*\&\^%$#@!~`´°¤§±×÷⇒→←↑↓│¦∗⊗∘\[\]{}()<>\\/\\|\\-]+$")
_NUMERIC_ONLY = re.compile(r"^[\s\d.,:;()\-–—〜~]+$")
_PAGE_MARKER = re.compile(r"^(page|p\.?|pp\.?)[\s\divxlc]+$", re.IGNORECASE)
_ISBN = re.compile(r"^isbn\b", re.IGNORECASE)


def _is_letter(char: str) -> bool:
    return char.isalpha()


def should_auto_copy(segment: Segment) -> bool:
    text = (segment.source_content or "").strip()
    if not text:
        return True

    if _PUNCT_ONLY.match(text):
        return True

    if _NUMERIC_ONLY.match(text):
        return True

    if _PAGE_MARKER.match(text):
        return True

    if _ISBN.match(text):
        return True

    if len(text) <= 3 and not any(_is_letter(ch) for ch in text):
        return True

    return False


__all__ = ["should_auto_copy"]
