"""Text polishing functions using cjk-text-formatter.

This module provides backward-compatible wrappers around cjk-text-formatter
for use in TEPUB's translation pipeline.
"""

from __future__ import annotations

from cjk_text_formatter.polish import CHINESE_RE, polish_text

from state.models import SegmentStatus, StateDocument

# Alias for backward compatibility
polish_translation = polish_text


def target_is_chinese(language: str) -> bool:
    """Check if target language is Chinese.

    Args:
        language: Language name or code

    Returns:
        True if language is Chinese, False otherwise
    """
    lower = language.lower()
    if "chinese" in lower:
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
