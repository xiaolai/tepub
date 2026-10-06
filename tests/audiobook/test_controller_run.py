"""AudiobookRunner decisions: which settings render, what is queued, when it stops.

The speech engine, segment renderer and final assembly are replaced; extraction,
state handling, queueing, cooldown and cover-only checks are the real code.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openai
import pytest
from rich.console import Console

from audiobook import controller
from audiobook import renderer as renderer_module
from audiobook.controller import (
    AudiobookIncompleteError,
    AudiobookRunner,
    SegmentWork,
    _build_audiobook_dashboard,
    _preview,
    _synthesize_segment,
)
from audiobook.models import AudioSegmentStatus
from audiobook.renderer import CouldntEncodeError, SegmentRenderer
from audiobook.state import load_state, mark_status, save_state
from audiobook.tts import TTSEngine
from config import AppSettings
from exceptions import WorkspaceBusyError
from extraction.pipeline import run_extraction
from state.store import load_segments, save_segments
from state.writer import exclusive_run
from tests.epub_builder import build_epub


class FakeRenderer:
    """Writes a placeholder file per segment; ``fail`` names segments that error."""

    calls: list[str] = []
    fail: set[str] = set()
    block: dict[str, threading.Event] = {}
    raises: dict[str, Exception] = {}

    def __init__(self, engine, sentence_pause_range, epub_reader=None) -> None:
        pass

    def render_segment(self, segment_id, sentences, output_dir, stop=None):
        FakeRenderer.calls.append(segment_id)
        gate = FakeRenderer.block.get(segment_id)
        if gate is not None:
            assert gate.wait(10)
        if segment_id in FakeRenderer.raises:
            raise FakeRenderer.raises[segment_id]
        if segment_id in FakeRenderer.fail:
            raise RuntimeError(f"provider refused [/bold] {segment_id}")
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{segment_id}.m4a"
        path.write_bytes(b"audio")
        return path, 1.0


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    FakeRenderer.calls = []
    FakeRenderer.fail = set()
    FakeRenderer.block = {}
    FakeRenderer.raises = {}
    engines: list[dict] = []
    assembled: list[set[str]] = []

    def fake_engine(**kwargs):
        engines.append(kwargs)
        return object()

    def fake_assemble(**kwargs):
        assembled.append(kwargs["included_segment_ids"])
        return kwargs["output_root"] / "book.m4a"

    monkeypatch.setattr(controller, "create_tts_engine", fake_engine)
    monkeypatch.setattr(controller, "SegmentRenderer", FakeRenderer)
    monkeypatch.setattr(controller, "assemble_audiobook", fake_assemble)
    monkeypatch.setattr(controller, "RETRY_DELAY_SECONDS", 0)

    epub = build_epub(
        tmp_path / "book.epub",
        [("one.xhtml", "One", "<p>Alpha speaks.</p><p>Beta speaks.</p>"),
         ("two.xhtml", "Two", "<p>Gamma speaks.</p><p>Delta speaks.</p>")],
    )
    settings = AppSettings(work_dir=tmp_path / "work", audiobook_workers=1)
    run_extraction(settings, epub)
    ids = [segment.segment_id for segment in load_segments(settings.segments_file).segments]

    def runner(**kwargs) -> AudiobookRunner:
        options = {"voice": "en-US-GuyNeural", "language": "en"}
        options.update(kwargs)
        return AudiobookRunner(settings=settings, input_epub=epub, **options)

    class Book:
        pass

    result = Book()
    result.settings, result.ids, result.runner = settings, ids, runner
    result.engines, result.assembled = engines, assembled
    return result


def _state_path(book) -> Path:
    return book.runner().state_path


def test_openai_without_a_model_uses_the_engine_default(book) -> None:
    book.runner(voice="nova", tts_provider="openai").run()

    assert book.engines[-1]["model"] == "tts-1"
    state = load_state(book.settings.work_dir / "audiobook@openaitts" / "audio_state.json")
    assert state.session.tts_model == "tts-1"


def test_resumed_run_keeps_the_sessions_prosody(book) -> None:
    book.runner(rate="+10%", volume="-5%").run()
    rendered = list(FakeRenderer.calls)

    book.runner().run()

    assert book.engines[-1]["rate"] == "+10%"
    assert book.engines[-1]["volume"] == "-5%"
    # Nothing was invalidated, so nothing was synthesised again.
    assert FakeRenderer.calls == rendered


def test_new_session_records_prosody_before_rendering(book) -> None:
    book.runner(rate="+10%", volume="-5%").run()

    session = load_state(_state_path(book)).session
    assert (session.rate, session.volume) == ("+10%", "-5%")
    assert (book.engines[0]["rate"], book.engines[0]["volume"]) == ("+10%", "-5%")


def test_resumed_run_keeps_the_sessions_speed(book) -> None:
    """No --tts-speed and none configured: the stored speed stands."""
    book.runner(tts_speed=1.5).run()
    rendered = list(FakeRenderer.calls)

    book.runner().run()

    assert book.engines[-1]["speed"] == 1.5
    assert load_state(_state_path(book)).session.tts_speed == 1.5
    assert FakeRenderer.calls == rendered


def _edit_text(book, segment_id: str, text: str) -> None:
    document = load_segments(book.settings.segments_file)
    for segment in document.segments:
        if segment.segment_id == segment_id:
            segment.source_content = text
    save_segments(document, book.settings.segments_file)


def test_edited_text_at_the_same_position_is_resynthesised(book) -> None:
    book.runner().run()
    FakeRenderer.calls = []

    _edit_text(book, book.ids[0], "Alpha now says something else.")
    book.runner().run()

    assert FakeRenderer.calls == [book.ids[0]]


def test_audio_from_before_text_digests_is_kept_and_backfilled(book) -> None:
    book.runner().run()
    path = _state_path(book)
    digests = {seg_id: record.text_sha256 for seg_id, record in load_state(path).segments.items()}
    assert all(digests.values())
    state = load_state(path)
    for record in state.segments.values():
        record.text_sha256 = None
    save_state(state, path)
    FakeRenderer.calls = []

    book.runner().run()

    assert FakeRenderer.calls == []
    backfilled = load_state(path).segments
    assert {seg_id: record.text_sha256 for seg_id, record in backfilled.items()} == digests


def test_cover_only_ignores_audio_settings(book) -> None:
    book.runner().run()

    book.runner(voice="en-US-JennyNeural", tts_speed=1.5, cover_only=True).run()

    state = load_state(_state_path(book))
    assert state.session.voice == "en-US-GuyNeural"
    assert all(
        record.status == AudioSegmentStatus.COMPLETED for record in state.segments.values()
    )
    assert len(book.assembled) == 2


def test_cover_only_accepts_deliberately_skipped_segments(book) -> None:
    book.runner().run()
    mark_status(_state_path(book), book.ids[0], AudioSegmentStatus.SKIPPED, audio_path=None)

    book.runner(cover_only=True).run()

    assert len(book.assembled) == 2


def test_cover_only_without_audio_fails(book) -> None:
    with pytest.raises(AudiobookIncompleteError):
        book.runner(cover_only=True).run()


def test_previously_skipped_segment_is_synthesised_once_speakable(book) -> None:
    book.runner().run()
    mark_status(
        _state_path(book), book.ids[1], AudioSegmentStatus.SKIPPED,
        audio_path=None, last_error="translation skip",
    )
    FakeRenderer.calls = []

    book.runner().run()

    assert FakeRenderer.calls == [book.ids[1]]
    assert load_state(_state_path(book)).segments[book.ids[1]].status == (
        AudioSegmentStatus.COMPLETED
    )


def test_persisted_cooldown_is_honoured_before_any_request(book, monkeypatch) -> None:
    book.runner().run()
    path = _state_path(book)
    state = load_state(path)
    for record in state.segments.values():
        record.status = AudioSegmentStatus.PENDING
    state.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    save_state(state, path)
    FakeRenderer.calls = []

    class WaitedError(Exception):
        pass

    def sleep(seconds):
        raise WaitedError

    monkeypatch.setattr(controller.time, "sleep", sleep)
    with pytest.raises(WaitedError):
        book.runner().run()

    assert FakeRenderer.calls == []


def test_remaining_errors_fail_the_run(book) -> None:
    FakeRenderer.fail = {book.ids[0]}

    with pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    assert book.assembled == []


def test_failed_segment_is_not_reported_as_synthesised(book, monkeypatch) -> None:
    FakeRenderer.fail = set(book.ids)
    monkeypatch.setattr(AudiobookRunner, "_wait_out_cooldown", lambda *a, **k: None)
    with controller.console.capture() as captured, pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    assert "Synthesised" not in captured.get()


def test_results_finishing_during_cooldown_are_saved_before_waiting(
    book, monkeypatch
) -> None:
    book.settings = book.settings.model_copy(update={"audiobook_workers": 4})
    runner = AudiobookRunner(
        settings=book.settings, input_epub=book.runner().input_epub,
        voice="en-US-GuyNeural", language="en",
    )
    FakeRenderer.fail = set(book.ids[:3])
    cooldown_began = threading.Event()
    FakeRenderer.block = {book.ids[3]: cooldown_began}
    real_set_cooldown = controller.set_cooldown

    def set_cooldown(path, until):
        real_set_cooldown(path, until)
        if until is not None:
            cooldown_began.set()

    seen_at_wait: list[AudioSegmentStatus] = []

    def wait_out(self, until, on_tick=None):
        seen_at_wait.append(load_state(self.state_path).segments[book.ids[3]].status)

    monkeypatch.setattr(controller, "set_cooldown", set_cooldown)
    monkeypatch.setattr(AudiobookRunner, "_wait_out_cooldown", wait_out)

    with pytest.raises(AudiobookIncompleteError):
        runner.run()

    assert seen_at_wait[0] == AudioSegmentStatus.COMPLETED


def test_second_run_on_the_same_workspace_is_refused(book) -> None:
    runner = book.runner()
    with exclusive_run(runner.state_path), pytest.raises(WorkspaceBusyError):
        runner.run()


def test_worker_stops_retrying_once_cooldown_begins() -> None:
    stop = threading.Event()
    calls = []

    class Refusing:
        def render_segment(self, segment_id, sentences, output_dir, stop=None):
            calls.append(segment_id)
            stop.set()
            raise RuntimeError("rate limited")

    work = SegmentWork(segment=type("S", (), {"segment_id": "s1"})(), sentences=["Hi."])
    started = time.monotonic()
    result = _synthesize_segment(work, Refusing(), Path("."), max_attempts=3, stop=stop)

    assert result.deferred and result.error is None
    assert calls == ["s1"]
    assert time.monotonic() - started < 5


def test_dashboard_shows_markup_in_book_text_literally() -> None:
    panel = _build_audiobook_dashboard(
        total_segments=1, completed_segments=0, skipped_segments=0, error_segments=0,
        pending_segments=1, preview_lines=[_preview("a [/bold] b [red]c", "green")],
    )
    console = Console(width=120, record=True)
    console.print(panel)

    assert "a [/bold] b [red]c" in console.export_text()


def test_renderer_stops_between_sentences_once_cooldown_begins(
    tmp_path, monkeypatch
) -> None:
    stop = threading.Event()
    spoken: list[str] = []

    class Engine(TTSEngine):
        def synthesize(self, text, output_path):
            spoken.append(text)
            stop.set()

    monkeypatch.setattr(renderer_module.AudioSegment, "from_file", lambda path: object())
    work = SegmentWork(
        segment=type("S", (), {"segment_id": "s1"})(), sentences=["One.", "Two.", "Three."]
    )
    result = _synthesize_segment(
        work, SegmentRenderer(Engine(), (0.0, 0.0)), tmp_path, max_attempts=3, stop=stop
    )

    assert result.deferred and result.error is None
    assert spoken == ["One."]


def _failing_once_per_call(error: Exception):
    calls: list[str] = []

    class Failing:
        def render_segment(self, segment_id, sentences, output_dir, stop=None):
            calls.append(segment_id)
            raise error

    return Failing(), calls


def test_local_file_error_is_not_retried(monkeypatch) -> None:
    monkeypatch.setattr(controller, "RETRY_DELAY_SECONDS", 0)
    work = SegmentWork(segment=type("S", (), {"segment_id": "s1"})(), sentences=["Hi."])
    renderer, calls = _failing_once_per_call(
        PermissionError(13, "Permission denied", "/book/segments/s1.m4a")
    )

    result = _synthesize_segment(work, renderer, Path("."), max_attempts=3)

    assert calls == ["s1"]
    assert isinstance(result.error, PermissionError) and result.permanent


def test_network_error_is_still_retried(monkeypatch) -> None:
    monkeypatch.setattr(controller, "RETRY_DELAY_SECONDS", 0)
    work = SegmentWork(segment=type("S", (), {"segment_id": "s1"})(), sentences=["Hi."])
    renderer, calls = _failing_once_per_call(ConnectionResetError(54, "reset by peer"))

    result = _synthesize_segment(work, renderer, Path("."), max_attempts=3)

    assert calls == ["s1"] * 3
    assert not result.permanent


def _openai_error(cls: type[openai.OpenAIError]) -> openai.OpenAIError:
    """An instance of an openai error class, made without its HTTP objects.

    Errors are classified by class alone. Their constructors take the HTTP
    library openai depends on, which changed from httpx to httpx2 between
    versions, so building real responses tied the tests to one of them.
    """
    error = cls.__new__(cls)
    Exception.__init__(error, "rejected")
    return error


PERMANENT_ERRORS = [
    pytest.param(_openai_error(openai.AuthenticationError), id="openai-401"),
    pytest.param(_openai_error(openai.PermissionDeniedError), id="openai-403"),
    pytest.param(_openai_error(openai.BadRequestError), id="openai-400"),
    pytest.param(_openai_error(openai.NotFoundError), id="openai-404"),
    pytest.param(_openai_error(openai.UnprocessableEntityError), id="openai-422"),
    pytest.param(OSError(28, "No space left on device"), id="enospc-no-filename"),
    pytest.param(OSError(30, "Read-only file system"), id="erofs-no-filename"),
    pytest.param(CouldntEncodeError("ffmpeg returned 1"), id="encode"),
]

TRANSIENT_ERRORS = [
    pytest.param(_openai_error(openai.RateLimitError), id="openai-429"),
    pytest.param(_openai_error(openai.InternalServerError), id="openai-503"),
    pytest.param(
        _openai_error(openai.APIConnectionError),
        id="openai-connection",
    ),
    pytest.param(TimeoutError("timed out"), id="timeout"),
]


@pytest.mark.parametrize("error", PERMANENT_ERRORS)
def test_permanent_error_is_not_retried(monkeypatch, error) -> None:
    monkeypatch.setattr(controller, "RETRY_DELAY_SECONDS", 0)
    work = SegmentWork(segment=type("S", (), {"segment_id": "s1"})(), sentences=["Hi."])
    renderer, calls = _failing_once_per_call(error)

    result = _synthesize_segment(work, renderer, Path("."), max_attempts=3)

    assert calls == ["s1"]
    assert result.error is error and result.permanent


@pytest.mark.parametrize("error", TRANSIENT_ERRORS)
def test_transient_provider_error_is_retried(monkeypatch, error) -> None:
    monkeypatch.setattr(controller, "RETRY_DELAY_SECONDS", 0)
    work = SegmentWork(segment=type("S", (), {"segment_id": "s1"})(), sentences=["Hi."])
    renderer, calls = _failing_once_per_call(error)

    result = _synthesize_segment(work, renderer, Path("."), max_attempts=3)

    assert calls == ["s1"] * 3
    assert not result.permanent


def test_rejected_credentials_do_not_start_a_cooldown(book, monkeypatch) -> None:
    FakeRenderer.raises = {
        seg_id: _openai_error(openai.AuthenticationError) for seg_id in book.ids
    }
    waited: list[datetime] = []
    monkeypatch.setattr(
        AudiobookRunner,
        "_wait_out_cooldown",
        lambda self, until, on_tick=None: waited.append(until),
    )

    with pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    state = load_state(_state_path(book))
    assert waited == [] and state.cooldown_until is None
    assert state.consecutive_failures == 0
    assert FakeRenderer.calls == book.ids


def test_local_file_errors_do_not_start_a_cooldown(book, monkeypatch) -> None:
    FakeRenderer.raises = {
        seg_id: OSError(28, "No space left on device", f"/segments/{seg_id}.m4a")
        for seg_id in book.ids
    }
    waited: list[datetime] = []
    monkeypatch.setattr(
        AudiobookRunner,
        "_wait_out_cooldown",
        lambda self, until, on_tick=None: waited.append(until),
    )

    with pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    state = load_state(_state_path(book))
    assert waited == [] and state.cooldown_until is None
    assert state.consecutive_failures == 0
    assert FakeRenderer.calls == book.ids


def test_selection_with_nothing_to_speak_fails(book, monkeypatch) -> None:
    monkeypatch.setattr(controller, "audiobook_segments", lambda settings, segments: [])

    with pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    assert book.assembled == []


def test_assembly_that_writes_nothing_fails_the_run(book, monkeypatch) -> None:
    book.runner().run()
    monkeypatch.setattr(controller, "assemble_audiobook", lambda **kwargs: None)

    with pytest.raises(AudiobookIncompleteError):
        book.runner(cover_only=True).run()


def test_synthesis_reads_the_selection_once(book, monkeypatch) -> None:
    reads: list[int] = []
    real = controller.audiobook_segments

    def counted(settings, segments):
        reads.append(1)
        return real(settings, segments)

    monkeypatch.setattr(controller, "audiobook_segments", counted)
    book.runner().run()

    assert len(reads) == 1
    assert book.assembled == [set(book.ids)]


def test_a_permanent_failure_is_not_tried_again_on_later_passes(book) -> None:
    """Another segment's success started a new pass, which retried a segment
    whose failure no retry can fix."""
    doomed = book.ids[0]
    FakeRenderer.raises = {doomed: OSError(28, "No space left on device", "/segments/x.m4a")}

    with pytest.raises(AudiobookIncompleteError):
        book.runner().run()

    assert FakeRenderer.calls.count(doomed) == 1


@pytest.mark.parametrize(("rate", "volume"), [("fast", None), (None, "50%")])
def test_an_edge_engine_refuses_prosody_it_would_fail_on(rate, volume) -> None:
    """Only the command line checked rate and volume: a stored session's
    value reached the workers, which failed on it and retried."""
    from audiobook.tts import InvalidProsodyError, create_tts_engine

    with pytest.raises(InvalidProsodyError):
        create_tts_engine("edge", "en-US-GuyNeural", rate=rate, volume=volume)
