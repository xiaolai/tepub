from __future__ import annotations

from collections import Counter

from rich.table import Table

from config import AppSettings

from .common import console, load_all_segments


def list_files(settings: AppSettings) -> None:
    segments_doc = load_all_segments(settings)
    counter = Counter(segment.file_path.as_posix() for segment in segments_doc.segments)

    table = Table(title="Segment Counts per File")
    table.add_column("File")
    table.add_column("Segments")
    for file_path, count in sorted(counter.items()):
        table.add_row(file_path, str(count))

    console.print(table)
