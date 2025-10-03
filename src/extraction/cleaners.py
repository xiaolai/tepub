from __future__ import annotations

import re

from lxml import html


def strip_spans_and_links(fragment: str) -> str:
    element = html.fragment_fromstring(fragment, create_parent=True)
    for node in list(element.iter()):
        if isinstance(node.tag, str) and node.tag.lower() in {"span", "a"}:
            node.drop_tag()
    return "".join(
        html.tostring(child, encoding="unicode") if isinstance(child.tag, str) else child
        for child in element
    )


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def normalize_punctuation(text: str) -> str:
    """Normalize punctuation patterns for consistency.

    Handles common typographical variations:
    - Spaced ellipsis (. . . or . . . .) → ...
    - Ensures single space after ellipsis when followed by text

    Args:
        text: Text to normalize

    Returns:
        Text with normalized punctuation
    """
    # Replace spaced dots (. . . or . . . .) with standard ellipsis
    text = re.sub(r"\.\s+\.\s+\.(?:\s+\.)*", "...", text)
    # Ensure exactly one space after ellipsis when followed by non-whitespace
    text = re.sub(r"\.\.\.\s*(?=\S)", "... ", text)
    return text
