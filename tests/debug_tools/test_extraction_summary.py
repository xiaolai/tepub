from __future__ import annotations

from pathlib import Path

from rich.console import Console

from config import AppSettings
from debug_tools.extraction_summary import print_extraction_summary
from state.models import (
    Segment,
    SegmentMetadata,
    SegmentsDocument,
    SegmentStatus,
    SkippedDocument,
    StateDocument,
    TranslationRecord,
)
from state.store import save_segments, save_state


def _make_segment(idx: int) -> Segment:
    return Segment(
        segment_id=f"seg-{idx}",
        file_path=Path(f"Text/chap{idx}.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode="text",  # type: ignore[arg-type]
        source_content=f"Paragraph {idx}",
        metadata=SegmentMetadata(element_type="p", spine_index=idx, order_in_file=1),
    )


def test_print_extraction_summary_outputs_tables(monkeypatch, tmp_path):
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)

    segments = [_make_segment(i) for i in range(4)]
    document = SegmentsDocument(
        epub_path=tmp_path / "book.epub",
        generated_at="2025-09-26T00:00:00Z",
        segments=segments,
        skipped_documents=[
            SkippedDocument(
                file_path=Path("Text/front.xhtml"),
                reason="cover",
                source="toc",
            )
        ],
    )
    save_segments(document, settings.segments_file)

    statuses = {
        0: SegmentStatus.COMPLETED,
        1: SegmentStatus.PENDING,
        2: SegmentStatus.PENDING,
        3: SegmentStatus.PENDING,
    }
    state = StateDocument(
        segments={
            segment.segment_id: TranslationRecord(
                segment_id=segment.segment_id,
                status=statuses[index],
            )
            for index, segment in enumerate(segments)
        }
    )
    save_state(state, settings.state_file)

    test_console = Console(record=True)
    monkeypatch.setattr("debug_tools.extraction_summary.console", test_console)

    print_extraction_summary(settings, show_samples=2)

    output = test_console.export_text()
    assert "Extraction Summary" in output
    assert "Text/front.xhtml" in output
    # Three pending units, but only the requested number of samples.
    assert "Pending segments: 3 (showing up to 2)" in output
    assert "seg-1" in output and "seg-2" in output
    assert "seg-3" not in output
