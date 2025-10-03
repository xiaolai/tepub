from __future__ import annotations

import re

from state.models import SegmentStatus, StateDocument

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
SPACE_RE = re.compile(r" ")


def contains_chinese(text: str) -> bool:
    return bool(CHINESE_RE.search(text))


def target_is_chinese(language: str) -> bool:
    lower = language.lower()
    if "chinese" in lower:
        return True
    return contains_chinese(language)


def _replace_dash(text: str) -> str:
    """Convert -- to —— with proper spacing."""
    def repl(match: re.Match[str]) -> str:
        before = match.group(1)
        after = match.group(2)
        left_space = "" if before == "）" else " "
        right_space = "" if after == "（" else " "
        return f"{before}{left_space}——{right_space}{after}"

    return re.sub(r"([^\s])--([^\s])", repl, text)


def _fix_emdash_spacing(text: str) -> str:
    """Fix spacing around existing —— (em-dash) characters."""
    def repl(match: re.Match[str]) -> str:
        before = match.group(1)
        after = match.group(2)
        left_space = "" if before == "）" else " "
        right_space = "" if after == "（" else " "
        return f"{before}{left_space}——{right_space}{after}"

    return re.sub(r"([^\s])\s*——\s*([^\s])", repl, text)


def _fix_quotes(text: str) -> str:
    text = re.sub(r"([A-Za-z0-9\u4e00-\u9fff])“", r"\1 “", text)
    text = re.sub(r"”([A-Za-z0-9\u4e00-\u9fff])", r"” \1", text)
    return text


def _space_between(text: str) -> str:
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9]+)", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9]+)([\u4e00-\u9fff])", r"\1 \2", text)
    return text


def _normalize_ellipsis(text: str) -> str:
    """Normalize spaced ellipsis patterns to standard ellipsis.

    Handles patterns like ". . ." or ". . . ." that might appear in AI translations.
    This is a universal rule applied to all languages.

    Args:
        text: Text to normalize

    Returns:
        Text with normalized ellipsis
    """
    # Replace spaced dots (. . . or . . . .) with standard ellipsis
    text = re.sub(r"\.\s+\.\s+\.(?:\s+\.)*", "...", text)
    # Ensure exactly one space after ellipsis when followed by non-whitespace
    text = re.sub(r"\.\.\.\s*(?=\S)", "... ", text)
    return text


def polish_translation(text: str) -> str:
    # Universal normalization (applies to all languages)
    text = _normalize_ellipsis(text)

    # Chinese-specific polishing
    if not contains_chinese(text):
        return text.strip()

    text = _replace_dash(text)
    text = _fix_emdash_spacing(text)
    text = _fix_quotes(text)
    text = _space_between(text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def polish_state(state: StateDocument) -> StateDocument:
    updated_state = state.model_copy(deep=True)
    for record in updated_state.segments.values():
        if record.status != SegmentStatus.COMPLETED or not record.translation:
            continue
        record.translation = polish_translation(record.translation)
    return updated_state


def polish_if_chinese(
    state_file_path,
    target_language: str,
    *,
    load_fn,
    save_fn,
    console_print,
    message_prefix: str = "",
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

    Returns:
        True if state was polished and saved, False otherwise
    """
    if not target_is_chinese(target_language):
        return False

    try:
        state = load_fn(state_file_path)
    except FileNotFoundError:
        return False

    polished = polish_state(state)
    if polished.model_dump() == state.model_dump():
        return False

    prefix = f"{message_prefix} " if message_prefix else ""
    console_print(f"[cyan]{prefix}Formatting translated text for Chinese typography…[/cyan]")
    save_fn(polished, state_file_path)
    console_print("[green]Formatting complete.[/green]")
    return True
