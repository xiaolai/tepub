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

    # Cheap pre-check so the "formatting…" message is only printed when there is
    # work to do; the authoritative read happens under the lock below.
    try:
        state = load_fn(state_file_path)
    except FileNotFoundError:
        return False

    if polish_state(state).model_dump() == state.model_dump():
        return False

    prefix = f"{message_prefix} " if message_prefix else ""
    console_print(f"[cyan]{prefix}Formatting translated text for Chinese typography…[/cyan]")

    # Re-read and write under the state-file lock. This was an unlocked
    # load-modify-save, so a translation completing between the read above and
    # the write below was overwritten by the stale snapshot.
    from state.store import update_state_atomic

    changed = update_state_atomic(state_file_path, polish_state)
    if changed:
        console_print("[green]Formatting complete.[/green]")
    return changed
