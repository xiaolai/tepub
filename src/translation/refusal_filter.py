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

# A refusal declines the task, so it names the task as the object of its own
# refusal. A task word elsewhere in the sentence is ordinary prose: "I cannot
# help smiling at that translation" is a reader enjoying a translation, not a
# model declining one. The translating act therefore has to follow the refusal
# predicate itself ("cannot translate", "unable to translate"), or be the
# object of an explicit construction ("cannot provide a translation", "help
# with the translation"). A bare "translation" or "translat" match used to
# flag any sentence that mentioned one.
_DECLINES = r"(?:cannot|can't|cant|can not|won't|will not|unable|not able)"
_NAMES_THE_TASK = re.compile(
    rf"{_DECLINES}\s+(?:to\s+)?translate\b"
    rf"|{_DECLINES}\s+(?:to\s+)?(?:provide|give|offer|produce|create)\s+"
    rf"(?:a|the|this|that|any)\s+translation\b"
    # "that", "this" and "it" name the task only when they stand alone: in
    # "I cannot help with that heavy suitcase" the pronoun is a determiner
    # introducing an ordinary object, not the thing being declined.
    r"|(?:help|assist)(?: you)? with (?:that|this|it)\b(?=\s*(?:[.!?,;:…\"')\]]|$))"
    r"|(?:help|assist)(?: you)? with (?:the|your|a|that|this) "
    r"(?:translation|request|task|text|passage|content)\b"
    r"|comply with (?:that|this|it|the request|your request)\b"
    r"|(?:complete|fulfil|fulfill|process|handle) (?:this|that|the|your) "
    r"(?:request|translation)\b"
    # Chinese: likewise, the translating act is the object of the refusal.
    r"|(?:无法|無法|不能|不会|不會|拒绝|拒絕)\s*(?:翻译|翻譯)"
    r"|(?:协助|協助|帮助|幫助)(?:处理|處理)"
    r"|(?:完成|处理|處理)(?:这个|這个|该|該|这|這|此)(?:请求|請求|内容|內容)"
)

# Openers that announce a model, which no ordinary sentence in a book does.
# The word boundary keeps "As an airman…" from matching "as an ai".
_SELF_IDENTIFYING = re.compile(r"^as an? (?:ai|artificial intelligence|language model)\b")

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

    if _SELF_IDENTIFYING.match(prefix_window):
        return True

    names_the_task = _NAMES_THE_TASK.search(prefix_window) is not None

    if prefix_window.startswith(_DIRECT_REFUSALS):
        return names_the_task

    # An apology counts only when the same passage also declines the task.
    if prefix_window.startswith(_APOLOGY_OPENERS):
        declines = any(marker in prefix_window for marker in _REFUSAL_MARKERS)
        return declines and names_the_task

    return False


__all__ = ["looks_like_refusal"]
