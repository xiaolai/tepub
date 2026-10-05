"""Which segments an audiobook covers: the one rule, used everywhere.

The controller filtered segments by the audiobook_files inclusion list or by
skip metadata, while the chapter preview grouped every stored segment, so the
preview listed chapters the book would never contain.
"""

from __future__ import annotations

from config import AppSettings
from state.models import Segment


def audiobook_segments(settings: AppSettings, segments: list[Segment]) -> list[Segment]:
    """The inclusion list if one is set, otherwise everything not skipped."""
    if settings.audiobook_files is not None:
        allowed = set(settings.audiobook_files)
        return [segment for segment in segments if segment.file_path.as_posix() in allowed]
    return [segment for segment in segments if segment.skip_reason is None]
