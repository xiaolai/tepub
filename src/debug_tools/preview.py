from __future__ import annotations

from pathlib import Path

from config import AppSettings
from epub_io.selector import collect_skip_candidates

from .common import console


def preview_skip_candidates(settings: AppSettings, input_epub: Path) -> None:
    candidates = collect_skip_candidates(input_epub, settings)

    if not candidates:
        console.print("[green]No skip candidates detected.[/green]")
        return

    console.print("[bold]Skip Candidates[/bold]")
    for candidate in candidates:
        console.print(
            f"- {candidate.file_path.as_posix()} :: {candidate.reason} (source={candidate.source})"
        )
