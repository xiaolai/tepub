"""Matching a table-of-contents entry to the heading it points at.

A TOC entry usually names the heading it targets, but the fragment it carries
is often not the heading's own id: in a converted book the id sits on a
<section> wrapper. The heading's text is then the only reliable link between
the two, so both the injector and the writer key on it.
"""

from __future__ import annotations


def title_key(text: str | None) -> str | None:
    """A normalised lookup key for a heading's text, or None when it is empty.

    The ``text:`` prefix keeps it out of the fragment-id namespace, where a
    heading could plausibly be called ``text:...``.
    """
    if not text:
        return None
    normalised = " ".join(text.split()).casefold()
    return f"text:{normalised}" if normalised else None
