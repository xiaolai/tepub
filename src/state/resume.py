from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .models import ResumeInfo, Segment, SegmentStatus, StateDocument
from .store import compute_resume_info, ensure_state, load_state, mark_status


def init_state_if_needed(
    state_path: Path,
    segments: Iterable[Segment],
    provider_name: str,
    model_name: str,
    source_language: str,
    target_language: str,
) -> StateDocument:
    return ensure_state(
        state_path,
        segments,
        provider_name,
        model_name,
        source_language,
        target_language,
    )


def load_resume_info(state_path: Path) -> ResumeInfo:
    if not state_path.exists():
        return ResumeInfo(remaining_segments=[], completed_segments=[], skipped_segments=[])
    return compute_resume_info(load_state(state_path))


def reset_segment(state_path: Path, segment_id: str) -> None:
    mark_status(state_path, segment_id, SegmentStatus.PENDING, translation=None, error_message=None)
