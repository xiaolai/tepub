from __future__ import annotations

from textwrap import dedent

from state.models import ExtractMode, Segment

from .languages import describe_language

# Default system prompt used when no custom prompt is configured
DEFAULT_SYSTEM_PROMPT = """
You are an expert translator and always excellent at preserving fidelity.
{language_instruction}
{mode_instruction}
Avoid repeating the source text or adding explanations unless strictly necessary for comprehension.
""".strip()


_PROMPT_PREAMBLE: str | None = None


def configure_prompt(preamble: str | None) -> None:
    """Configure custom prompt preamble.

    Args:
        preamble: Custom prompt text with optional placeholders:
                 {source_language}, {target_language}, {mode_instruction}
                 Pass None to use the built-in DEFAULT_SYSTEM_PROMPT.
    """
    global _PROMPT_PREAMBLE
    _PROMPT_PREAMBLE = preamble.strip() if preamble else None


def build_prompt(segment: Segment, source_language: str, target_language: str) -> str:
    """Build the complete translation prompt for a segment.

    Args:
        segment: The segment to translate
        source_language: Source language code or 'auto'
        target_language: Target language code

    Returns:
        Complete prompt with system instructions and source content
    """
    mode_instruction = (
        "Preserve HTML structure in the translation."
        if segment.extract_mode == ExtractMode.HTML
        else "Return a faithful translation of the prose without adding explanations."
    )
    display_source = describe_language(source_language)
    display_target = describe_language(target_language)

    if source_language == "auto" or display_source.lower() == "auto":
        language_instruction = (
            f"Detect the source language automatically and translate it into {display_target}."
        )
    else:
        language_instruction = f"Translate from {display_source} into {display_target}."

    # Use custom prompt if configured, otherwise use default
    if _PROMPT_PREAMBLE:
        intro = dedent(
            _PROMPT_PREAMBLE.format(
                source_language=display_source,
                target_language=display_target,
                mode_instruction=mode_instruction,
            )
        ).strip()
    else:
        intro = DEFAULT_SYSTEM_PROMPT.format(
            language_instruction=language_instruction,
            mode_instruction=mode_instruction,
        ).strip()

    return f"{intro}\n\nSOURCE:\n{segment.source_content}"
