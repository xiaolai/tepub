"""How a translate run counts, stops, cools down and reports."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from rich.console import Console

from config import AppSettings
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument, SegmentStatus
from state.store import ensure_state, load_state, save_segments
from state.writer import StateWriter
from translation import controller
from translation.controller import RunSummary, plan_translation, run_translation
from translation.providers import ProviderError, ProviderFatalError


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings().model_copy(update={"work_dir": tmp_path, "translation_workers": 1})
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def epub(tmp_path) -> Path:
    path = tmp_path / "book.epub"
    path.write_text("stub", encoding="utf-8")
    return path


@pytest.fixture
def console(monkeypatch) -> Console:
    out = Console(record=True, width=200)
    monkeypatch.setattr("translation.controller.console", out)
    return out


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr("translation.controller._sleep", calls.append)
    return calls


def _segments(settings: AppSettings, epub: Path, *texts: str) -> list[Segment]:
    segments = [
        Segment(
            segment_id=f"s-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=text,
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i, text in enumerate(texts, start=1)
    ]
    save_segments(
        SegmentsDocument(epub_path=epub, generated_at="2024-01-01T00:00:00Z", segments=segments),
        settings.segments_file,
    )
    return segments


def _provider(monkeypatch, translate, *, name="dummy"):
    class Provider:
        model = "dummy-model"

        def preflight(self):
            pass

    Provider.name = name
    Provider.translate = staticmethod(translate)
    instance = Provider()
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: instance)
    return instance


def _statuses(settings: AppSettings) -> dict[str, SegmentStatus]:
    return {k: r.status for k, r in load_state(settings.state_file).segments.items()}


def _record_progress(monkeypatch) -> list[int]:
    reports: list[int] = []

    class Recorder:
        def __init__(self, label, total, **_kwargs):
            self.total = total

        def report(self, done):
            assert done <= self.total
            reports.append(done)

    monkeypatch.setattr("translation.controller.PlainProgress", Recorder)
    return reports


def test_progress_counts_successes_not_attempts(monkeypatch, settings, epub, console):
    """A failure and its retry each advanced progress, so "Translated: 2 of 2"
    was printed while a unit was still failing."""
    _segments(settings, epub, "First sentence.", "Second sentence.")
    failed_once: set[str] = set()

    def translate(segment, source_language, target_language):
        if segment.segment_id == "s-1" and "s-1" not in failed_once:
            failed_once.add("s-1")
            raise ProviderError("429 rate limited")
        return "Hola"

    _provider(monkeypatch, translate)
    reports = _record_progress(monkeypatch)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert reports == [1, 2]


def test_progress_ignores_records_outside_the_selection(monkeypatch, settings, epub, console):
    """A completed record for a unit no longer selected started the count above zero."""
    segments = _segments(settings, epub, "First sentence.")
    stale = segments[0].model_copy(update={"segment_id": "dropped"})
    ensure_state(settings.state_file, [segments[0], stale], "dummy", "dummy-model", "en", "es")
    with StateWriter(settings.state_file) as writer:
        writer.mark("dropped", SegmentStatus.COMPLETED, translation="x")
    _provider(monkeypatch, lambda *a, **k: "Hola")
    reports = _record_progress(monkeypatch)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert reports == [1]


def test_a_finished_book_needs_no_provider(monkeypatch, settings, epub, console):
    segments = _segments(settings, epub, "First sentence.")
    ensure_state(settings.state_file, segments, "dummy", "dummy-model", "en", "es")
    with StateWriter(settings.state_file) as writer:
        writer.mark("s-1", SegmentStatus.COMPLETED, translation="Hola")

    def unreachable(_config):
        raise ProviderFatalError("OPENAI_API_KEY missing")

    monkeypatch.setattr("translation.controller.create_provider", unreachable)

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    assert summary.completed == 1 and summary.ok


def test_units_copied_as_they_are_need_no_provider(monkeypatch, settings, epub, console):
    _segments(settings, epub, "…", "42")

    def unreachable(_config):
        raise ProviderFatalError("Cannot reach Ollama")

    monkeypatch.setattr("translation.controller.create_provider", unreachable)

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    assert set(_statuses(settings).values()) == {SegmentStatus.COMPLETED}
    assert summary.translated_now == 2


def test_a_language_change_counts_the_retranslated_units(monkeypatch, settings, epub, console):
    """The units keep their IDs after a reset, so "completed after minus before"
    reported none translated in this run."""
    segments = _segments(settings, epub, "First sentence.")
    ensure_state(settings.state_file, segments, "dummy", "dummy-model", "en", "fr")
    with StateWriter(settings.state_file) as writer:
        writer.mark("s-1", SegmentStatus.COMPLETED, translation="Bonjour")
    _provider(monkeypatch, lambda *a, **k: "Hola")

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    assert summary.translated_now == 1


def test_the_summary_checks_the_glossary_in_the_run_language(monkeypatch, settings, epub, console):
    """The summary used the settings' language, so a run into another one,
    with a glossary for it, raised GlossaryError after translating the book."""
    _segments(settings, epub, "Hello world.")
    (settings.work_dir / "glossary.yaml").write_text(
        "target_language: es\nterms:\n  - source: world\n    target: mundo\n", encoding="utf-8"
    )
    assert settings.target_language == "Simplified Chinese"
    _provider(monkeypatch, lambda *a, **k: "Hola mundo.")

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    assert summary.completed == 1


def test_a_success_already_running_at_a_fatal_stop_is_kept(monkeypatch, settings, epub, console):
    """The run stopped reading results at a fatal error, so a call that was
    already running finished, was paid for, and was thrown away."""
    settings = settings.model_copy(update={"translation_workers": 2})
    _segments(settings, epub, "Fails once the other runs.", "Finishes later.")
    second_running = threading.Event()

    def translate(segment, source_language, target_language):
        if segment.segment_id == "s-1":
            assert second_running.wait(timeout=10)
            raise ProviderFatalError("key revoked")
        second_running.set()
        time.sleep(0.5)
        return "Hola"

    _provider(monkeypatch, translate)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert _statuses(settings) == {"s-1": SegmentStatus.ERROR, "s-2": SegmentStatus.COMPLETED}


def test_one_pass_takes_at_most_one_cooldown(monkeypatch, settings, epub, console, slept):
    """Failures from calls already running when a cooldown began each started
    another, so the run waited past its cap."""
    settings = settings.model_copy(update={"translation_workers": 2})
    _segments(settings, epub, "First sentence.", "Second sentence.")
    together = threading.Barrier(2, timeout=10)

    def translate(segment, source_language, target_language):
        together.wait()
        raise ProviderError("429 rate limited")

    _provider(monkeypatch, translate)
    monkeypatch.setattr("translation.controller.FAILURES_BEFORE_COOLDOWN", 1)

    run_translation(settings, epub, source_language="en", target_language="es")

    expected = controller.MAX_COOLDOWNS_PER_RUN * controller.COOLDOWN.total_seconds()
    assert sum(slept) == expected


def test_the_run_tries_again_after_its_last_cooldown(monkeypatch, settings, epub, console, slept):
    """The cap was checked after waiting, so the last cooldown's wait was
    followed by a stop rather than another attempt."""
    _segments(settings, epub, "First sentence.")
    calls = []

    def translate(segment, source_language, target_language):
        calls.append(segment.segment_id)
        raise ProviderError("429 rate limited")

    _provider(monkeypatch, translate)
    monkeypatch.setattr("translation.controller.FAILURES_BEFORE_COOLDOWN", 1)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert len(calls) == controller.MAX_COOLDOWNS_PER_RUN + 1


def test_an_error_in_tepub_itself_starts_no_cooldown(monkeypatch, settings, epub, console, slept):
    _segments(settings, epub, "One.", "Two.", "Three.", "Four.")

    def translate(segment, source_language, target_language):
        raise TypeError("a bug, not the provider")

    _provider(monkeypatch, translate)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert slept == []
    record = load_state(settings.state_file).segments["s-1"]
    assert record.status == SegmentStatus.ERROR
    assert "TypeError" in record.error_message


def test_an_empty_fenced_reply_is_not_a_translation(monkeypatch, settings, epub, console):
    _segments(settings, epub, "Hello world.")
    _provider(monkeypatch, lambda *a, **k: "```\n```")

    run_translation(settings, epub, source_language="en", target_language="es")

    assert _statuses(settings) == {"s-1": SegmentStatus.ERROR}


def test_markup_in_a_reply_is_shown_as_text(monkeypatch, settings, epub):
    """A reply containing "[/boom]" was read as Rich markup and crashed the dashboard."""
    terminal = Console(record=True, force_terminal=True, width=200)
    monkeypatch.setattr("translation.controller.console", terminal)
    _segments(settings, epub, "Hello world.")
    _provider(monkeypatch, lambda *a, **k: "Hola [/boom] mundo")

    run_translation(settings, epub, source_language="en", target_language="es")

    assert "Hola [/boom] mundo" in terminal.export_text()


def test_the_retry_command_names_the_book_as_given(monkeypatch, settings, tmp_path, console):
    book = tmp_path / "my books" / "A Book.epub"
    book.parent.mkdir()
    book.write_text("stub", encoding="utf-8")
    _segments(settings, book, "Hello world.")

    def translate(segment, source_language, target_language):
        raise ProviderFatalError("key revoked")

    _provider(monkeypatch, translate)

    run_translation(settings, book, source_language="en", target_language="es")

    assert f"tepub translate '{book}'" in console.export_text()


def test_a_summary_with_units_left_is_not_ok():
    summary = RunSummary(
        total=2, completed=1, failed=(), pending=1, translated_now=1, seconds=0, glossary_misses=0
    )
    assert not summary.ok


def test_a_dry_run_counts_units_a_language_change_resets(settings, epub):
    segments = _segments(settings, epub, "Hello world.")
    ensure_state(settings.state_file, segments, "dummy", "dummy-model", "en", "fr")
    with StateWriter(settings.state_file) as writer:
        writer.mark("s-1", SegmentStatus.COMPLETED, translation="Bonjour")

    same = settings.model_copy(update={"source_language": "en", "target_language": "fr"})
    other = settings.model_copy(update={"source_language": "en", "target_language": "es"})
    # Compared as codes, as the run compares them: a name is no language change.
    spelled = settings.model_copy(
        update={"source_language": "English", "target_language": "French"}
    )

    assert plan_translation(same, epub).units == 0
    assert plan_translation(spelled, epub).units == 0
    assert plan_translation(other, epub).units == 1


def test_a_fatal_error_stops_the_calls_within_a_few(monkeypatch, settings, epub, console):
    """Every pending unit was submitted at once, so one worker went on calling
    the provider for the whole book before the fatal error was read."""
    _segments(settings, epub, *(f"Sentence {i}." for i in range(1000)))
    calls: list[str] = []

    def translate(segment, source_language, target_language):
        calls.append(segment.segment_id)
        raise ProviderFatalError("key revoked")

    _provider(monkeypatch, translate)

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    # The failed call, and at most the few queued behind it.
    assert len(calls) <= controller.SUBMITTED_PER_WORKER * settings.translation_workers < 10
    assert summary.pending >= 1000 - len(calls)


def test_a_cooldown_stops_the_calls_within_a_few(monkeypatch, settings, epub, console):
    _segments(settings, epub, *(f"Sentence {i}." for i in range(200)))
    calls: list[str] = []
    at_cooldown: list[int] = []

    def translate(segment, source_language, target_language):
        calls.append(segment.segment_id)
        raise ProviderError("429 rate limited")

    def sleep(_seconds):
        if not at_cooldown:
            at_cooldown.append(len(calls))

    _provider(monkeypatch, translate)
    monkeypatch.setattr("translation.controller._sleep", sleep)
    monkeypatch.setattr("translation.controller.FAILURES_BEFORE_COOLDOWN", 1)
    monkeypatch.setattr("translation.controller.MAX_COOLDOWNS_PER_RUN", 1)

    run_translation(settings, epub, source_language="en", target_language="es")

    assert at_cooldown and at_cooldown[0] <= controller.SUBMITTED_PER_WORKER < 10
    # One pass before the cooldown and one after, each stopped within a few calls.
    assert len(calls) <= 2 * controller.SUBMITTED_PER_WORKER < 20


def test_an_error_in_tepub_itself_is_not_retried(monkeypatch, settings, epub, console, slept):
    """Another unit's success started a second pass, which retried the bug as
    if it were a provider hiccup."""
    _segments(settings, epub, "Broken.", "Fine.")
    calls: list[str] = []

    def translate(segment, source_language, target_language):
        calls.append(segment.segment_id)
        if segment.segment_id == "s-1":
            raise TypeError("a bug, not the provider")
        return "Hola"

    _provider(monkeypatch, translate)

    summary = run_translation(settings, epub, source_language="en", target_language="es")

    assert calls.count("s-1") == 1
    assert _statuses(settings) == {"s-1": SegmentStatus.ERROR, "s-2": SegmentStatus.COMPLETED}
    assert summary.failed == ("s-1",)


def test_the_dashboard_counts_only_running_calls(monkeypatch):
    from concurrent.futures import Future

    shown: dict = {}

    def capture(**kwargs):
        shown.update(kwargs)

    monkeypatch.setattr("translation.controller._build_dashboard_panel", capture)
    view = controller._Dashboard([], {}, skipped_files=0, max_workers=4)
    queued, running, finished, cancelled = Future(), Future(), Future(), Future()
    running.set_running_or_notify_cancel()
    finished.set_running_or_notify_cancel()
    finished.set_result(None)
    cancelled.cancel()
    view.in_flight = dict.fromkeys((queued, running, finished, cancelled))

    view._render_panel()

    assert shown["active_workers"] == 1


def test_a_language_change_is_recorded_when_every_unit_is_skipped(settings, epub, console):
    """The run returned before reconciling languages, so a reused workspace
    kept the old target and export named its files after it."""
    segments = [
        s.model_copy(update={"skip_reason": "cover", "skip_source": "rule"})
        for s in _segments(settings, epub, "Hello world.")
    ]
    save_segments(
        SegmentsDocument(epub_path=epub, generated_at="2024-01-01T00:00:00Z", segments=segments),
        settings.segments_file,
    )
    ensure_state(settings.state_file, segments, "dummy", "dummy-model", "en", "fr")
    with StateWriter(settings.state_file) as writer:
        writer.mark("s-1", SegmentStatus.COMPLETED, translation="Bonjour", source_sha256="d")

    run_translation(settings, epub, source_language="en", target_language="es")

    state = load_state(settings.state_file)
    assert (state.source_language, state.target_language) == ("en", "es")
    assert state.segments["s-1"].status == SegmentStatus.PENDING
    assert state.segments["s-1"].source_sha256 == "d"
