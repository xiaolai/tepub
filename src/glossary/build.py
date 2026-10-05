"""Proposed glossary entries for a person to review: terms, renderings, context.

`tepub glossary build` writes them to glossary.proposed.yaml. Nothing is used
until it is saved as glossary.yaml: deciding renderings before translating is
the step every professional workflow has, and letting the first translation
decide is what gave one book ten names for one term.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .model import Term

PROPOSED_FILE = "glossary.proposed.yaml"

_PARENTHESES = re.compile(r"\s*[(（][^)）]*[)）]")


@dataclass(frozen=True)
class Proposal:
    source: str
    count: int
    context: str
    target: str | None = None


def context_of(source: str, text: str, *, width: int = 240) -> str:
    """The sentence where the term first occurs, cut to ``width`` characters."""
    match = Term(source=source).pattern.search(text)
    if match is None:
        return ""
    start = max(
        max(text.rfind(mark, 0, match.start()) for mark in (". ", "? ", "! ", "\n")) + 1,
        match.start() - width // 2,
    )
    ends = [i for i in (text.find(mark, match.end()) for mark in (". ", "? ", "! ", "\n")) if i >= 0]
    end = min([*ends, match.end() + width // 2, len(text)])
    return " ".join(text[start : end + 1].split())


def clean_rendering(reply: str, source: str) -> str | None:
    """A model's rendering of a term, or None when the reply is not one.

    Models add romanisation in parentheses and closing punctuation; a reply far
    longer than the term is a sentence, not a rendering, and is left for the
    person to fill in.
    """
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    if not lines:
        return None
    rendering = _PARENTHESES.sub("", lines[0]).strip("\"'“”‘’「」《》。.，,;；:： \t")
    if not rendering or len(rendering) > max(3 * len(source), 24):
        return None
    return rendering


def propose(
    found: list[tuple[str, int]],
    text: str,
    translate: Callable[[str, str], str] | None,
    *,
    progress: Callable[[int], None] | None = None,
) -> list[Proposal]:
    """Proposals for each found term; with ``translate(term, context)``, a rendering too."""
    proposals = []
    for done, (source, count) in enumerate(found, start=1):
        context = context_of(source, text)
        target = clean_rendering(translate(source, context), source) if translate else None
        proposals.append(Proposal(source, count, context, target))
        if progress is not None:
            progress(done)
    return proposals


def _quoted(value: str) -> str:
    # JSON strings are valid YAML double-quoted scalars, whatever they hold.
    return json.dumps(value, ensure_ascii=False)


def render_proposals(proposals: list[Proposal], target_language: str) -> str:
    """glossary.proposed.yaml: entries with their count and context as comments."""
    lines = [
        "# Proposed by `tepub glossary build`. Review every entry: correct or fill in",
        "# each target, delete the terms you do not want held to one rendering, then",
        "# save the file as glossary.yaml. An entry without a target is not used.",
        "# A target equal to the source keeps the term untranslated.",
        f"target_language: {_quoted(target_language)}",
        "terms:",
    ]
    for proposal in proposals:
        lines.append(f"  # {proposal.count}x: {proposal.context}")
        lines.append(f"  - source: {_quoted(proposal.source)}")
        if proposal.target:
            lines.append(f"    target: {_quoted(proposal.target)}")
    return "\n".join(lines) + "\n"
