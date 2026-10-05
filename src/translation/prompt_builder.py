from __future__ import annotations

from textwrap import dedent

from config.placeholders import fill_placeholders
from state.models import ExtractMode, Segment

from .languages import describe_language
from .markup import has_markers

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
    if segment.extract_mode == ExtractMode.HTML:
        mode_instruction = (
            "The source is an HTML fragment. Translate only its text. Keep every tag, and "
            "every href, src and id value, exactly as given; inline elements may move to "
            "follow the translated wording."
        )
    elif has_markers(segment.source_content):
        mode_instruction = (
            "The source contains numbered markers such as ⟦1⟧ and ⟦/1⟧. Keep every "
            "marker exactly once, unchanged: a pair ⟦n⟧…⟦/n⟧ goes around the translated "
            "words it wrapped, and a single ⟦n⟧ stays where it belongs in the sentence. "
            "Return a faithful translation without adding explanations."
        )
    else:
        mode_instruction = "Return a faithful translation of the prose without adding explanations."
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
        # {language_instruction} is documented as an available placeholder in both
        # README.md and config.example.yaml, but was never passed here, so any
        # custom prompt_preamble using it failed with KeyError.
        # Only these names are filled; any other brace in the user's text is
        # left alone (see config.placeholders).
        intro = dedent(
            fill_placeholders(
                _PROMPT_PREAMBLE,
                {
                    "source_language": display_source,
                    "target_language": display_target,
                    "mode_instruction": mode_instruction,
                    "language_instruction": language_instruction,
                },
            )
        ).strip()
    else:
        intro = DEFAULT_SYSTEM_PROMPT.format(
            language_instruction=language_instruction,
            mode_instruction=mode_instruction,
        ).strip()

    note = segment.metadata.notes
    if note:
        # Set when a previous reply broke the markup contract; see the controller.
        intro = f"{intro}\n\nNOTE: {note}"
    return f"{intro}\n\nSOURCE:\n{segment.source_content}"
