import threading
from pathlib import Path

import pytest
from rich.console import Console

from config import AppSettings
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentsDocument, SegmentStatus
from state.store import load_state, save_segments
from translation import controller
from translation.controller import run_translation
from translation.providers.base import ProviderFatalError


class DummyProvider:
    name = "dummy"
    model = "dummy-model"

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        return "Hola mundo"  # plain text: the source is a text unit


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings().model_copy(update={"work_dir": tmp_path})
    cfg.ensure_directories()
    return cfg


def _write_segments(settings: AppSettings, input_epub: Path) -> Segment:
    segment = Segment(
        segment_id="chapter1-001",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello world",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    document = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2024-01-01T00:00:00Z",
        segments=[segment],
        skipped_documents=[],
    )
    save_segments(document, settings.segments_file)
    return segment


def test_run_translation_updates_state(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)

    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())

    # A terminal shows the live dashboard.
    test_console = Console(record=True, force_terminal=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    record = state.segments[segment.segment_id]
    assert record.status == SegmentStatus.COMPLETED
    assert record.translation == "Hola mundo"
    assert record.provider_name == "dummy"
    assert record.model_name == "dummy-model"

    output = test_console.export_text()
    assert "Dashboard" in output
    assert "files" in output
    assert "Hola mundo" in output


def test_without_a_terminal_progress_is_plain_lines(monkeypatch, settings, tmp_path):
    """A live dashboard drawn into a log file filled it with escape codes."""
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _write_segments(settings, input_epub)
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())
    log = Console(record=True, force_terminal=False)
    monkeypatch.setattr("translation.controller.console", log)

    run_translation(settings, input_epub, source_language="en", target_language="zh-CN")

    output = log.export_text()
    assert "Dashboard" not in output and "\x1b" not in output
    assert "Translated: 1 of 1" in output


def test_quiet_prints_nothing(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _write_segments(settings, input_epub)
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())
    quiet = Console(record=True, force_terminal=True, quiet=True)
    monkeypatch.setattr("translation.controller.console", quiet)

    run_translation(settings, input_epub, source_language="en", target_language="zh-CN")

    assert quiet.export_text() == ""


class FailingProvider:
    name = "dummy"
    model = "dummy-model"

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        raise ProviderFatalError("network unavailable")


def test_run_translation_handles_fatal_error(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "fatal.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)

    monkeypatch.setattr("translation.controller.create_provider", lambda _config: FailingProvider())

    test_console = Console(record=True)
    monkeypatch.setattr("translation.controller.console", test_console)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    record = state.segments[segment.segment_id]
    assert record.status == SegmentStatus.ERROR

    # Error should be logged
    output = test_console.export_text()
    assert "network unavailable" in output.lower()


class SpyProvider:
    name = "spy"
    model = "spy-model"

    def __init__(self):
        self.calls = []

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        self.calls.append(segment.source_content)
        return "TRANSLATED"


def test_run_translation_auto_copies_punctuation(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "auto-copy.epub"
    input_epub.write_text("stub", encoding="utf-8")

    ellipsis_segment = Segment(
        segment_id="seg-ellipsis",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="…",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    content_segment = Segment(
        segment_id="seg-content",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[2]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello world",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=2),
    )

    document = SegmentsDocument(
        epub_path=input_epub,
        generated_at="2024-01-01T00:00:00Z",
        segments=[ellipsis_segment, content_segment],
        skipped_documents=[],
    )
    save_segments(document, settings.segments_file)

    spy = SpyProvider()
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: spy)

    run_translation(
        settings,
        input_epub,
        source_language="en",
        target_language="zh-CN",
    )

    state = load_state(settings.state_file)
    ellipsis_record = state.segments[ellipsis_segment.segment_id]
    assert ellipsis_record.status == SegmentStatus.COMPLETED
    assert ellipsis_record.translation == "…"
    assert ellipsis_record.provider_name is None  # Auto-copied segments have no provider
    assert ellipsis_record.model_name is None

    content_record = state.segments[content_segment.segment_id]
    assert content_record.provider_name == "spy"  # AI-translated segments have provider info
    assert content_record.model_name == "spy-model"

    assert spy.calls == ["Hello world"]


class FlakyProvider:
    """Fails the first three calls the way a rate-limited API does, then works."""

    name = "dummy"
    model = "dummy-model"

    def __init__(self) -> None:
        self.calls = 0

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        from translation.providers.base import ProviderError

        self.calls += 1
        if self.calls <= 3:
            raise ProviderError("429 rate limited")
        return "Hola"


def test_a_cooldown_resumes_the_run(monkeypatch, settings, tmp_path):
    """After a cooldown the run carries on; it used to wait 30 minutes and quit."""
    from state.models import SegmentsDocument

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = [
        Segment(
            segment_id=f"c-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence number {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, 6)
    ]
    save_segments(
        SegmentsDocument(
            epub_path=input_epub, generated_at="2024-01-01T00:00:00Z", segments=segments
        ),
        settings.segments_file,
    )
    settings = settings.model_copy(update={"translation_workers": 1})
    provider = FlakyProvider()
    slept: list[float] = []
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: provider)
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    state = load_state(settings.state_file)
    assert {r.status for r in state.segments.values()} == {SegmentStatus.COMPLETED}
    assert slept, "a cooldown should have been taken"


class AlwaysRateLimited:
    name = "dummy"
    model = "dummy-model"

    def preflight(self):
        pass

    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        from translation.providers.base import ProviderError

        raise ProviderError("429 rate limited")


def test_cooldowns_are_capped(monkeypatch, settings, tmp_path):
    """A provider that never recovers ends the run after a bounded wait."""
    from translation import controller

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _write_segments(settings, input_epub)
    settings = settings.model_copy(update={"translation_workers": 1})
    slept: list[float] = []
    monkeypatch.setattr(
        "translation.controller.create_provider", lambda _config: AlwaysRateLimited()
    )
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)
    monkeypatch.setattr("translation.controller.FAILURES_BEFORE_COOLDOWN", 1)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    expected = controller.MAX_COOLDOWNS_PER_RUN * controller.COOLDOWN.total_seconds()
    assert sum(slept) == expected
    state = load_state(settings.state_file)
    assert {r.status for r in state.segments.values()} == {SegmentStatus.ERROR}


def test_a_language_change_backs_up_and_says_so(monkeypatch, settings, tmp_path):
    from state.store import ensure_state
    from state.writer import StateWriter

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)
    ensure_state(settings.state_file, [segment], "dummy", "dummy-model", "en", "fr")
    with StateWriter(settings.state_file) as writer:
        writer.mark(segment.segment_id, SegmentStatus.COMPLETED, translation="Bonjour")

    console = Console(record=True)
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())
    monkeypatch.setattr("translation.controller.console", console)

    run_translation(settings, input_epub, source_language="en", target_language="zh-CN")

    backups = [p for p in settings.state_file.parent.glob("state.*.json")]
    assert len(backups) == 1
    assert load_state(backups[0]).segments[segment.segment_id].translation == "Bonjour"
    text = console.export_text()
    assert "1 finished translation" in text and str(backups[0].name) in text


def test_a_stopped_run_resumes_where_it_left_off(monkeypatch, settings, tmp_path):
    """After a fatal stop, the next run translates only what is still to do."""
    from state.models import SegmentsDocument

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = [
        Segment(
            segment_id=f"c-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence number {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, 6)
    ]
    save_segments(
        SegmentsDocument(
            epub_path=input_epub, generated_at="2024-01-01T00:00:00Z", segments=segments
        ),
        settings.segments_file,
    )
    settings = settings.model_copy(update={"translation_workers": 1})
    monkeypatch.setattr("translation.controller.console", Console(record=True))

    # With one worker, the worker may pick up c-4 before or after the run
    # reads c-3's fatal error and cancels what is queued. The events fix the
    # order: the run cancels only once c-4 is running, and c-4 finishes only
    # after the cancel, so c-4 is in flight (finished and kept) and c-5 is
    # queued (cancelled, left pending). c-4 is submitted when c-2's result is
    # recorded, so c-3 fails only after that: if c-2 and c-3 finished within
    # one wait, the stop was read first and c-4 was rightly never submitted.
    fourth_started = threading.Event()
    queued_cancelled = threading.Event()
    second_recorded = threading.Event()
    real_cancel = controller._cancel_queued
    real_record = controller._record_result

    def record_and_signal(segment, *args, **kwargs):
        outcome = real_record(segment, *args, **kwargs)
        if segment.segment_id == "c-2":
            second_recorded.set()
        return outcome

    monkeypatch.setattr("translation.controller._record_result", record_and_signal)

    def cancel_once_fourth_runs(futures):
        assert fourth_started.wait(5), "c-4 never started"
        real_cancel(futures)
        queued_cancelled.set()

    monkeypatch.setattr("translation.controller._cancel_queued", cancel_once_fourth_runs)

    class StopsOnThird:
        name, model = "dummy", "dummy-model"
        seen: list[str] = []

        def preflight(self):
            pass

        def translate(self, segment, source_language, target_language):
            StopsOnThird.seen.append(segment.segment_id)
            if segment.segment_id == "c-3":
                assert second_recorded.wait(5), "c-2 was never recorded"
                raise ProviderFatalError("key revoked")
            if segment.segment_id == "c-4":
                fourth_started.set()
                assert queued_cancelled.wait(5), "the queued call was never cancelled"
            return "Hola"

    class Counting:
        name, model = "dummy", "dummy-model"
        seen: list[str] = []

        def preflight(self):
            pass

        def translate(self, segment, source_language, target_language):
            Counting.seen.append(segment.segment_id)
            return "Hola"

    monkeypatch.setattr("translation.controller.create_provider", lambda _c: StopsOnThird())
    run_translation(settings, input_epub, source_language="en", target_language="es")
    first = load_state(settings.state_file)
    done_first = {k for k, r in first.segments.items() if r.status == SegmentStatus.COMPLETED}
    # The call in flight when the fatal error was read finishes and is kept;
    # the queued one is never made.
    assert StopsOnThird.seen == ["c-1", "c-2", "c-3", "c-4"]
    assert done_first == {"c-1", "c-2", "c-4"}

    monkeypatch.setattr("translation.controller.create_provider", lambda _c: Counting())
    run_translation(settings, input_epub, source_language="en", target_language="es")

    assert sorted(Counting.seen) == ["c-3", "c-5"]
    final = load_state(settings.state_file)
    assert {r.status for r in final.segments.values()} == {SegmentStatus.COMPLETED}


class RejectsEveryReply:
    """Answers every time, but each reply is refused for its content."""

    name = "dummy"
    model = "dummy-model"

    def preflight(self):
        pass

    def translate(self, segment, source_language, target_language):
        from translation.providers import ReplyRejectedError

        raise ReplyRejectedError(
            f"Translation of segment {segment.segment_id} changed the markup twice"
        )


def test_rejected_replies_do_not_start_a_cooldown(monkeypatch, settings, tmp_path):
    """Three replies in a row that changed their markup once stopped a run for
    30 minutes; the provider was answering fine, so waiting could not help."""
    from state.models import SegmentsDocument

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segments = [
        Segment(
            segment_id=f"r-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence number {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, 6)
    ]
    save_segments(
        SegmentsDocument(
            epub_path=input_epub, generated_at="2024-01-01T00:00:00Z", segments=segments
        ),
        settings.segments_file,
    )
    settings = settings.model_copy(update={"translation_workers": 1})
    slept: list[float] = []
    monkeypatch.setattr(
        "translation.controller.create_provider", lambda _config: RejectsEveryReply()
    )
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    state = load_state(settings.state_file)
    assert {r.status for r in state.segments.values()} == {SegmentStatus.ERROR}
    assert slept == [], "no cooldown for replies rejected on content"


def test_a_failure_count_left_by_an_earlier_run_starts_no_cooldown(monkeypatch, settings, tmp_path):
    """A run restored from a backup carried consecutive_failures=3; its first
    rejected reply started a 30-minute cooldown."""
    from state.store import ensure_state, save_state

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)
    state = ensure_state(settings.state_file, [segment], "dummy", "dummy-model", "en", "es")
    state.consecutive_failures = 3
    save_state(state, settings.state_file)
    slept: list[float] = []
    monkeypatch.setattr(
        "translation.controller.create_provider", lambda _config: RejectsEveryReply()
    )
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    assert slept == []


def test_one_language_spelled_two_ways_is_not_a_change(monkeypatch, settings, tmp_path):
    """ "Simplified Chinese" and "zh-CN" are one language; comparing spellings
    reset finished translations."""
    from state.store import ensure_state
    from state.writer import StateWriter

    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    segment = _write_segments(settings, input_epub)
    ensure_state(
        settings.state_file, [segment], "dummy", "dummy-model", "English", "Simplified Chinese"
    )
    with StateWriter(settings.state_file) as writer:
        writer.mark(
            segment.segment_id, SegmentStatus.COMPLETED, translation="你好", provider_name="x"
        )

    monkeypatch.setattr("translation.controller.create_provider", lambda _config: DummyProvider())
    monkeypatch.setattr("translation.controller.console", Console(record=True))

    run_translation(settings, input_epub, source_language="en", target_language="zh-CN")

    assert list(settings.state_file.parent.glob("state.*.json")) == []
    assert load_state(settings.state_file).segments[segment.segment_id].translation == "你好"


class LocalServer:
    """A local provider whose requests time out; its health check answers
    while ``up`` is True."""

    name = "dummy"
    model = "dummy-model"
    local = True

    def __init__(self, up_after_start: bool):
        self.checks = 0
        self.up_after_start = up_after_start

    def preflight(self):
        self.checks += 1
        if self.checks > 1 and not self.up_after_start:
            from translation.providers import ProviderFatalError

            raise ProviderFatalError("Cannot reach Ollama at http://localhost:11434")

    def translate(self, segment, source_language, target_language):
        from translation.providers import ProviderError

        raise ProviderError("Ollama request failed after 3 attempts: Read timed out")


def _five_segments(settings, input_epub):
    from state.models import SegmentsDocument

    segments = [
        Segment(
            segment_id=f"l-{i}",
            file_path=Path("Text/c.xhtml"),
            xpath=f"/html/body/p[{i}]",
            extract_mode=ExtractMode.TEXT,
            source_content=f"Sentence number {i}.",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
        )
        for i in range(1, 6)
    ]
    save_segments(
        SegmentsDocument(
            epub_path=input_epub, generated_at="2024-01-01T00:00:00Z", segments=segments
        ),
        settings.segments_file,
    )


def test_a_local_server_that_answers_is_not_waited_for(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _five_segments(settings, input_epub)
    settings = settings.model_copy(update={"translation_workers": 1})
    server = LocalServer(up_after_start=True)
    slept: list[float] = []
    monkeypatch.setattr("translation.controller.create_provider", lambda _config: server)
    monkeypatch.setattr("translation.controller.console", Console(record=True))
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    assert slept == []
    assert server.checks >= 2  # checked once at start, again when failures piled up
    assert {r.status for r in load_state(settings.state_file).segments.values()} == {
        SegmentStatus.ERROR
    }


def test_a_local_server_that_is_down_stops_the_run_with_the_fix(monkeypatch, settings, tmp_path):
    input_epub = tmp_path / "book.epub"
    input_epub.write_text("stub", encoding="utf-8")
    _five_segments(settings, input_epub)
    settings = settings.model_copy(update={"translation_workers": 1})
    console = Console(record=True)
    slept: list[float] = []
    monkeypatch.setattr(
        "translation.controller.create_provider", lambda _config: LocalServer(up_after_start=False)
    )
    monkeypatch.setattr("translation.controller.console", console)
    monkeypatch.setattr("translation.controller._sleep", slept.append)

    run_translation(settings, input_epub, source_language="en", target_language="es")

    assert slept == []
    assert "Cannot reach Ollama" in console.export_text()
    statuses = [r.status for r in load_state(settings.state_file).segments.values()]
    assert statuses.count(SegmentStatus.ERROR) == 3 and statuses.count(SegmentStatus.PENDING) == 2
