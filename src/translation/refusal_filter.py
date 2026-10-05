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

# A refusal declines the task, so it names the task. Without this, a book
# translated into English was full of false positives: "I cannot believe it",
# "I'm sorry, I can't come tonight".
_TASK_MARKERS: tuple[str, ...] = (
    "translat",
    "help",
    "assist",
    "comply",
    "request",
    "provide",
    "content",
    "翻译",
    "翻譯",
    "协助",
    "協助",
    "帮助",
    "幫助",
    "请求",
    "請求",
    "内容",
    "內容",
    "处理",
    "處理",
)

# Openers that announce a model, which no ordinary sentence in a book does.
_SELF_IDENTIFYING: tuple[str, ...] = ("as an ai", "as a language model")

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

    if prefix_window.startswith(_SELF_IDENTIFYING):
        return True

    names_the_task = any(marker in prefix_window for marker in _TASK_MARKERS)

    if prefix_window.startswith(_DIRECT_REFUSALS):
        return names_the_task

    # An apology counts only when the same passage also declines the task.
    if prefix_window.startswith(_APOLOGY_OPENERS):
        declines = any(marker in prefix_window for marker in _REFUSAL_MARKERS)
        return declines and names_the_task

    return False


__all__ = ["looks_like_refusal"]
