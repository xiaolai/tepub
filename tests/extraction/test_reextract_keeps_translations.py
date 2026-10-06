"""Re-running extract on a translated book keeps its translations.

Translate records language codes ("zh-CN"); extract passed the configured
names ("Simplified Chinese"), and the mismatch rebuilt the state as PENDING,
erasing a translated book, including right after a format upgrade had carried
every translation over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import AppSettings
from extraction.pipeline import run_extraction
from state.models import SegmentStatus
from state.store import load_state, save_state
from tests.epub_builder import build_epub


@pytest.mark.parametrize("format_upgrade", [False, True])
def test_reextracting_a_translated_book_keeps_every_translation(
    tmp_path: Path, format_upgrade: bool
) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work", target_language="Simplified Chinese")
    run_extraction(settings, book)
    state = load_state(settings.state_file)
    for segment_id, record in state.segments.items():
        state.segments[segment_id] = record.model_copy(
            update={
                "translation": "译",
                "status": SegmentStatus.COMPLETED,
                "provider_name": "ollama",
            }
        )
    state.source_language, state.target_language = "auto", "zh-CN"  # as translate records them
    save_state(state, settings.state_file)
    if format_upgrade:
        # Workspaces old enough to need migrating kept no source digests.
        _strip_digests(settings)
        data = json.loads(settings.segments_file.read_text(encoding="utf-8"))
        data["format_version"] = 3
        settings.segments_file.write_text(json.dumps(data), encoding="utf-8")

    run_extraction(settings, book)

    after = load_state(settings.state_file)
    assert len(after.segments) == 2
    assert {r.status for r in after.segments.values()} == {SegmentStatus.COMPLETED}
    assert {r.translation for r in after.segments.values()} == {"译"}


def _translate_all(settings: AppSettings) -> None:
    state = load_state(settings.state_file)
    for segment_id, record in state.segments.items():
        state.segments[segment_id] = record.model_copy(
            update={"translation": "译", "status": SegmentStatus.COMPLETED}
        )
    save_state(state, settings.state_file)


def test_an_edited_paragraph_loses_its_old_translation_and_the_rest_keep_theirs(
    tmp_path: Path,
) -> None:
    """Unit ids come from place, not text: an edited paragraph kept the old
    paragraph's translation and exported it as its own."""
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_all(settings)

    build_epub(book, [("c.xhtml", "C", "<p>One.</p><p>Something else.</p>")])
    run_extraction(settings, book)

    after = load_state(settings.state_file)
    by_text = {
        s["source_content"]: after.segments[s["segment_id"]]
        for s in json.loads(settings.segments_file.read_text(encoding="utf-8"))["segments"]
    }
    assert by_text["One."].status == SegmentStatus.COMPLETED
    assert by_text["Something else."].status == SegmentStatus.PENDING
    assert by_text["Something else."].translation is None
    assert list(settings.state_file.parent.glob("state.*.json")), "no backup kept"


def test_units_left_out_keep_their_translations_until_they_return_changed(
    tmp_path: Path,
) -> None:
    """A unit can drop out only because a skip rule changed; its finished
    translation is kept, but not trusted if the unit comes back different."""
    two = [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")]
    book = build_epub(tmp_path / "book.epub", two)
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_all(settings)
    document = json.loads(settings.segments_file.read_text("utf-8"))
    ids = [s["segment_id"] for s in document["segments"]]

    build_epub(book, [("c.xhtml", "C", "<p>One.</p>")])
    run_extraction(settings, book)
    kept = load_state(settings.state_file)
    assert {r.status for r in kept.segments.values()} == {SegmentStatus.COMPLETED}
    assert set(kept.segments) == set(ids)

    build_epub(book, two)
    run_extraction(settings, book)
    assert load_state(settings.state_file).segments[ids[1]].status == SegmentStatus.COMPLETED

    build_epub(book, [("c.xhtml", "C", "<p>One.</p>")])
    run_extraction(settings, book)
    build_epub(book, [("c.xhtml", "C", "<p>One.</p><p>Changed.</p>")])
    run_extraction(settings, book)
    after = load_state(settings.state_file)
    assert after.segments[ids[0]].status == SegmentStatus.COMPLETED
    assert after.segments[ids[1]].status == SegmentStatus.PENDING
    assert after.segments[ids[1]].translation is None


def _strip_digests(settings: AppSettings) -> None:
    state = load_state(settings.state_file)
    for record in state.segments.values():
        record.source_sha256 = None
    save_state(state, settings.state_file)


def test_a_state_from_before_digests_is_checked_against_the_last_extraction(
    tmp_path: Path,
) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p><p>Two.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_all(settings)
    _strip_digests(settings)

    build_epub(book, [("c.xhtml", "C", "<p>One.</p><p>Changed.</p>")])
    run_extraction(settings, book)

    after = sorted(load_state(settings.state_file).segments.values(), key=lambda r: r.segment_id)
    assert [r.status for r in after] == [SegmentStatus.COMPLETED, SegmentStatus.PENDING]
    assert all(r.source_sha256 for r in after)


def test_finished_records_with_nothing_to_check_them_against_are_reset(tmp_path: Path) -> None:
    """A state without its segments may be another book's: its translations
    were stamped as this book's and exported."""
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_all(settings)
    _strip_digests(settings)
    settings.segments_file.unlink()

    run_extraction(settings, book)

    after = load_state(settings.state_file)
    assert {r.status for r in after.segments.values()} == {SegmentStatus.PENDING}
    assert list(settings.state_file.parent.glob("state.*.json")), "no backup kept"


def test_extraction_refuses_while_a_run_holds_the_workspace(tmp_path: Path) -> None:
    """Extraction rewrote the state with no lock, beside a running translate."""
    from exceptions import WorkspaceBusyError
    from state.writer import exclusive_run

    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    with exclusive_run(settings.state_file), pytest.raises(WorkspaceBusyError):
        run_extraction(settings, book)


def test_corrupt_segments_are_reported_not_parsed_into_a_traceback(tmp_path: Path) -> None:
    from exceptions import CorruptedStateError

    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    settings.segments_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(CorruptedStateError):
        run_extraction(settings, book)


def test_the_book_is_recorded_by_absolute_path(tmp_path: Path, monkeypatch) -> None:
    """A relative path stopped status finding the book from another folder."""
    build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    monkeypatch.chdir(tmp_path)
    settings = AppSettings(work_dir=tmp_path / "work")

    run_extraction(settings, Path("book.epub"))

    recorded = json.loads(settings.segments_file.read_text("utf-8"))["epub_path"]
    assert Path(recorded) == (tmp_path / "book.epub").resolve()
