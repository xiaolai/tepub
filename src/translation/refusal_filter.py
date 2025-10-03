from __future__ import annotations

import re

_REFUSAL_PREFIXES: tuple[str, ...] = (
    "i'm sorry",
    "im sorry",
    "sorry, i",
    "sorry i",
    "i cannot",
    "i can't",
    "i cant",
    "抱歉，我",
    "抱歉，無法",
    "抱歉，无法",
    "抱歉，我無法",
    "抱歉，我无法",
    "抱歉，我不能",
    "抱歉，我不",
)

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("’", "'")
    text = text.replace("`", "'")
    text = _WHITESPACE.sub(" ", text)
    return text


def looks_like_refusal(text: str | None, *, max_length: int = 400) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False

    normalised = _normalise(stripped)
    prefix_window = normalised[:max_length]

    for prefix in _REFUSAL_PREFIXES:
        if prefix_window.startswith(prefix):
            return True

    return False


__all__ = ["looks_like_refusal"]
