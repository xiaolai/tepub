from __future__ import annotations

import re

# An apology is not a refusal. Openers alone used to be enough to match, so an
# ordinary translated line — "I'm sorry for your loss", "抱歉，我來晚了" — was
# classified as a provider refusal and reset to pending, discarding good work.
# The bare "抱歉，我" prefix also shadowed every longer 抱歉 variant below it,
# making those entries unreachable.
_APOLOGY_OPENERS: tuple[str, ...] = (
    "i'm sorry",
    "im sorry",
    "sorry, i",
    "sorry i",
    "i apologize",
    "i apologise",
    "抱歉",
    "对不起",
    "對不起",
    "很抱歉",
)

# A refusal opener needs no corroboration — it states the refusal outright.
_DIRECT_REFUSALS: tuple[str, ...] = (
    "i cannot",
    "i can't",
    "i cant",
    "i am unable",
    "i'm unable",
    "as an ai",
    "as a language model",
)

# Corroborating evidence that an apology is actually declining the task.
_REFUSAL_MARKERS: tuple[str, ...] = (
    "cannot",
    "can't",
    "cant",
    "unable",
    "not able",
    "won't",
    "will not",
    "無法",
    "无法",
    "不能",
    "不會",
    "不会",
    "拒絕",
    "拒绝",
    "无法完成",
    "無法完成",
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

    if prefix_window.startswith(_DIRECT_REFUSALS):
        return True

    # An apology counts only when the same passage also declines the task.
    if prefix_window.startswith(_APOLOGY_OPENERS):
        return any(marker in prefix_window for marker in _REFUSAL_MARKERS)

    return False


__all__ = ["looks_like_refusal"]
