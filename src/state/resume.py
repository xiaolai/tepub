from __future__ import annotations

from pathlib import Path

from .models import ResumeInfo
from .store import compute_resume_info, load_state


def load_resume_info(state_path: Path) -> ResumeInfo:
    if not state_path.exists():
        return ResumeInfo(remaining_segments=[], completed_segments=[], skipped_segments=[])
    return compute_resume_info(load_state(state_path))
