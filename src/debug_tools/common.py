from __future__ import annotations

from pathlib import Path

from config import AppSettings
from console_singleton import get_console
from state.store import load_segments, load_state

console = get_console()


def require_file(path: Path, description: str) -> None:
    if not path.exists():
        console.print(f"[bold red]{description} not found:[/bold red] {path}")
        raise SystemExit(1)


def load_all_segments(settings: AppSettings):
    require_file(settings.segments_file, "Segments file")
    return load_segments(settings.segments_file)


def load_translation_state(settings: AppSettings):
    require_file(settings.state_file, "State file")
    return load_state(settings.state_file)
