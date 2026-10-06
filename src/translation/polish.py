"""Text polishing functions using cjk-text-formatter.

This module provides backward-compatible wrappers around cjk-text-formatter
for use in TEPUB's translation pipeline.
"""

from __future__ import annotations

import re

from cjk_text_formatter.polish import CHINESE_RE
from cjk_text_formatter.polish import polish_text as _format
from rich.markup import escape

from state.models import SegmentStatus, StateDocument

# Full-width punctuation carries its own spacing. Models translating English
# keep the space that followed each English full stop, giving "改善。 那个";
# cjk-text-formatter has no rule for it.
_SPACE_AROUND_FULLWIDTH = re.compile(r"[ \t\u3000]*([。，、；：？！])[ \t\u3000]*")


# Translations are HTML fragments, or text carrying tepub's numbered markers
# (translation.markup). The typography rules were written for prose and ran over
# the whole string, so they rewrote attribute values: href="c.xhtml#中文2024"
# became "#中文 2024", breaking the link. Every piece of markup is set aside as
# one opaque character before formatting and put back afterwards: comments,
# CDATA, processing instructions (which may hold ">") and declarations, whose
# quoted literals and internal subset may hold ">"; start and end tags, with
# quoted attribute values that may hold ">"; markers; and elements whose content
# is code or not text at all, with that content.
_TOKEN = re.compile(
    r"<!--.*?-->"
    r"|<!\[CDATA\[.*?\]\]>"
    r"|<\?.*?\?>"
    r"|<!(?:[^\"'>\[]|\"[^\"]*\"|'[^']*'"
    r"|\[(?:[^\"'\]]|\"[^\"]*\"|'[^']*')*\])*>"
    r"|<[!?][^>]*>"
    r"|<(?P<end>/)?(?P<name>[A-Za-z][^\s\"'/>]*)"
    r"(?:[^\"'>]|\"[^\"]*\"|'[^']*')*?(?P<empty>/)?>"
    r"|⟦\s*/?\s*\d+\s*⟧",
    re.DOTALL,
)
# Content is raw text in script and style, so no tag can open inside them; code
# and its kin can hold more of themselves, <code>a<code>b</code>c</code>.
_RAW_TEXT = frozenset({"script", "style"})
_CODE = frozenset({"code", "pre", "kbd", "samp"})


def _element_end(text: str, start: int, name: str) -> int:
    """Where the element opened just before ``start`` ends, its end tag
    included; the end of the text when it is never closed, so that unclosed
    code is still not formatted."""
    if name in _RAW_TEXT:
        # XHTML parses script and style as XML: a CDATA section or comment in
        # them is opaque, so a "</script>" inside one does not end the element.
        pattern = rf"<!\[CDATA\[.*?\]\]>|<!--.*?-->|(?P<close></{name}\s*>)"
        for token in re.compile(pattern, re.IGNORECASE | re.DOTALL).finditer(text, start):
            if token.group("close"):
                return token.end()
        return len(text)
    depth = 1
    for token in _TOKEN.finditer(text, start):
        if (token.group("name") or "").lower() != name:
            continue
        if token.group("end"):
            depth -= 1
            if depth == 0:
                return token.end()
        elif not token.group("empty"):
            depth += 1
    return len(text)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0
    while token := _TOKEN.search(text, position):
        end = token.end()
        name = (token.group("name") or "").lower()
        opens = name in _RAW_TEXT | _CODE and not token.group("end")
        if opens and not token.group("empty"):
            end = _element_end(text, end, name)
        spans.append((token.start(), end))
        position = end
    return spans


# Unicode noncharacters: never part of interchanged text. The stand-in is
# neither a letter, a digit, CJK, punctuation nor whitespace to the rules, as
# "<" and ">" are not, so formatting next to a tag is what it was before: no
# space is added between "中文" and "<em>English</em>" or a note reference
# such as "<sup>1</sup>", and a space after "。" is still dropped before a tag.
_STAND_INS = [chr(code) for code in range(0xFDD0, 0xFDF0)]


def polish_text(text: str) -> str:
    """Chinese typography for the text of an HTML fragment, markup untouched."""
    spans = _protected_spans(text)
    if not spans:
        return _polish_prose(text)
    stand_in = next((char for char in _STAND_INS if char not in text), None)
    if stand_in is None:
        raise ValueError("Text holds every noncharacter polish could stand markup in for")
    pieces = [text[start:end] for start, end in spans]
    bounds = [0, *(i for span in spans for i in span), len(text)]
    masked = stand_in.join(text[bounds[i]:bounds[i + 1]] for i in range(0, len(bounds), 2))
    polished = _polish_prose(masked)
    parts = polished.split(stand_in)
    if len(parts) != len(pieces) + 1:
        raise RuntimeError(
            f"Typography formatting lost markup: {len(pieces)} pieces in, "
            f"{len(parts) - 1} out"
        )
    out = [parts[0]]
    for piece, part in zip(pieces, parts[1:], strict=True):
        out.append(piece)
        out.append(part)
    return "".join(out)


def _polish_prose(text: str) -> str:
    return _SPACE_AROUND_FULLWIDTH.sub(r"\1", _format(text))


# Alias for backward compatibility
polish_translation = polish_text


def target_is_chinese(language: str) -> bool:
    """Check if target language is Chinese.

    Args:
        language: Language name or code

    Returns:
        True if language is Chinese, False otherwise
    """
    lower = language.strip().lower()
    if "chinese" in lower:
        return True
    # ISO codes were not recognised at all, so a config using `target_language:
    # zh-CN` silently skipped Chinese typography formatting. Match the primary
    # subtag so every zh-* variant (zh, zh-CN, zh-TW, zh-Hans, zh-Hant) counts.
    if lower.replace("_", "-").split("-")[0] in {"zh", "cmn", "yue"}:
        return True
    return bool(CHINESE_RE.search(language))


def polish_state(state: StateDocument) -> StateDocument:
    """Polish all completed translations in a state document.

    Args:
        state: State document to polish

    Returns:
        New state document with polished translations
    """
    updated_state = state.model_copy(deep=True)
    for record in updated_state.segments.values():
        if record.status != SegmentStatus.COMPLETED or not record.translation:
            continue
        record.translation = polish_text(record.translation)
    return updated_state


def polish_if_chinese(
    state_file_path,
    target_language: str,
    *,
    load_fn,
    save_fn,
    console_print,
    message_prefix: str = "",
    holding_lock: bool = False,
) -> bool:
    """Polish state file if target language is Chinese and changes are needed.

    This consolidates the common pattern of:
    1. Check if target is Chinese
    2. Load state
    3. Polish it
    4. Compare for changes
    5. Save if changed
    6. Print status

    Args:
        state_file_path: Path to state file
        target_language: Target language string
        load_fn: Function to load state (e.g., load_state)
        save_fn: Function to save state (e.g., save_state)
        console_print: Console print function
        message_prefix: Optional prefix for console messages
        holding_lock: The caller holds the workspace lock (state.writer.exclusive_run),
            which cannot be taken twice; the state is then saved directly.

    Returns:
        True if state was polished and saved, False otherwise
    """
    if not target_is_chinese(target_language):
        return False

    # Cheap pre-check so the "formatting…" message is only printed when there is
    # work to do; the authoritative read happens under the lock below.
    try:
        state = load_fn(state_file_path)
    except FileNotFoundError:
        return False

    if polish_state(state).model_dump() == state.model_dump():
        return False

    prefix = f"{escape(message_prefix)} " if message_prefix else ""
    console_print(f"[cyan]{prefix}Formatting translated text for Chinese typography…[/cyan]")

    if holding_lock:
        # Nothing else can write the state while the caller holds the lock.
        save_fn(polish_state(state), state_file_path)
        changed = True
    else:
        # Re-read and write under the state-file lock. This was an unlocked
        # load-modify-save, so a translation completing between the read above
        # and the write below was overwritten by the stale snapshot.
        from state.writer import update_state_atomic

        changed = update_state_atomic(state_file_path, polish_state)
    if changed:
        console_print("[green]Formatting complete.[/green]")
    return changed
