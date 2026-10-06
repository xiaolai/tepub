from __future__ import annotations

import errno
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from config import AppSettings
from console_singleton import PlainProgress, get_console
from console_singleton import live as live_display
from exceptions import TepubError
from state.models import Segment, SegmentStatus
from state.store import load_segments
from state.store import load_state as load_translation_state
from state.writer import exclusive_run

from .assembly import assemble_audiobook
from .models import AudioSegmentState, AudioSegmentStatus, AudioStateDocument
from .preprocess import segment_to_text, split_sentences
from .renderer import CouldntEncodeError, RenderStoppedError, SegmentRenderer
from .selection import audiobook_segments
from .state import (
    apply_text_digests,
    ensure_state,
    get_or_create_segment,
    mark_status,
    reset_error_segments,
    save_state,
    set_consecutive_failures,
    set_cooldown,
)
from .state import (
    load_state as load_audio_state,
)
from .tts import create_tts_engine

try:
    import openai
except ImportError:  # optional; only the OpenAI engine needs it
    _OPENAI_PERMANENT: tuple[type[Exception], ...] = ()
else:
    # Statuses whose cause is the request or the account, not the moment:
    # 400, 401, 403, 404, 422. Rate limits, timeouts, connection errors and
    # 5xx stay retried.
    _OPENAI_PERMANENT = (
        openai.BadRequestError,
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
        openai.UnprocessableEntityError,
    )

#: OSError numbers that only a local filesystem reports; sockets never do.
_FILESYSTEM_ERRNOS = frozenset(
    code
    for code in (
        errno.ENOSPC,
        errno.EROFS,
        errno.EFBIG,
        getattr(errno, "EDQUOT", None),
    )
    if code is not None
)

console = get_console()

#: The OpenAI engine's own default. Passing model=None overrode it with None.
DEFAULT_OPENAI_MODEL = "tts-1"
RETRY_DELAY_SECONDS = 60
COOLDOWN_AFTER_FAILURES = 3
COOLDOWN = timedelta(minutes=30)
PREVIEW_LENGTH = 60


class AudiobookIncompleteError(TepubError):
    """The run ended without writing the requested audiobook."""


def _build_audiobook_dashboard(
    *,
    total_segments: int,
    completed_segments: int,
    skipped_segments: int,
    error_segments: int,
    pending_segments: int,
    preview_lines: list[Text],
    active_workers: int = 0,
    max_workers: int = 1,
    in_cooldown: bool = False,
    cooldown_remaining: str = "",
) -> Panel:
    """Build dashboard panel for audiobook synthesis."""
    stats = Table.grid(padding=(1, 1))
    stats.add_column(style="bold cyan", justify="left", no_wrap=True)
    stats.add_column(justify="left", no_wrap=True)

    stats.add_row(
        "segments",
        f"total {total_segments}, completed {completed_segments}, skipped {skipped_segments}",
    )
    stats.add_row(
        "",
        f"errors {error_segments}, pending {pending_segments}",
    )

    # Always show workers row (fixed height)
    if max_workers > 1:
        stats.add_row("workers", f"active {active_workers}/{max_workers}")
    else:
        stats.add_row("workers", "single worker")

    # Always show cooldown row (fixed height)
    if in_cooldown:
        stats.add_row(
            "cooldown",
            f"⏸  waiting {cooldown_remaining} ({COOLDOWN_AFTER_FAILURES} consecutive fails)",
        )
    else:
        stats.add_row("cooldown", "")  # Empty row to maintain height

    # ALWAYS show exactly max_workers preview lines (fixed height); the lines
    # arrive already truncated, see _preview.
    for i in range(max_workers):
        label = f"w{i+1}" if max_workers > 1 else "current"
        text = preview_lines[i] if i < len(preview_lines) else Text("")
        stats.add_row(label, text)

    return Panel(stats, border_style="magenta", title="Dashboard", padding=(1, 1))


def _truncate_text(text: str, max_length: int = 80) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def _preview(text: str, style: str = "") -> Text:
    # Book text and error messages are literal: as markup, a "[/x]" in them
    # raised MarkupError and stopped synthesis.
    return Text(_truncate_text(text, PREVIEW_LENGTH), style=style)


def _has_audio(seg_state: AudioSegmentState | None) -> bool:
    """Completed, with the audio file still there.

    The one test of "done" for the queue, the counters and cover-only checks;
    trusting the COMPLETED flag alone dropped segments whose file was deleted.
    """
    return (
        seg_state is not None
        and seg_state.status == AudioSegmentStatus.COMPLETED
        and seg_state.audio_path is not None
        and Path(seg_state.audio_path).exists()
    )


def _needs_audio(seg_state: AudioSegmentState | None) -> bool:
    if seg_state is not None and seg_state.status == AudioSegmentStatus.SKIPPED:
        return False
    return not _has_audio(seg_state)


def _tally(audio_state: AudioStateDocument, seg_ids) -> tuple[int, int, int]:
    """(completed, skipped, error) counts over ``seg_ids``."""
    completed = skipped = errors = 0
    for seg_id in seg_ids:
        seg_state = audio_state.segments.get(seg_id)
        if _has_audio(seg_state):
            completed += 1
        elif seg_state is not None and seg_state.status == AudioSegmentStatus.SKIPPED:
            skipped += 1
        elif seg_state is not None and seg_state.status == AudioSegmentStatus.ERROR:
            errors += 1
    return completed, skipped, errors


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class SynthesisResult:
    """Result of synthesizing audio for a single segment."""

    __slots__ = (
        "segment_id", "audio_path", "duration", "error", "attempts", "text_preview", "deferred",
        "permanent",
    )

    def __init__(
        self,
        segment_id: str,
        audio_path: Path | None = None,
        duration: float | None = None,
        error: Exception | None = None,
        attempts: int = 0,
        text_preview: str = "",
        deferred: bool = False,
        permanent: bool = False,
    ):
        self.segment_id = segment_id
        self.audio_path = audio_path
        self.duration = duration
        self.error = error
        self.attempts = attempts
        self.text_preview = text_preview
        # Stopped by a cooldown before finishing; the segment stays pending.
        self.deferred = deferred
        # Failed for a reason a retry cannot fix; see _is_permanent_error.
        self.permanent = permanent


def _is_permanent_error(exc: Exception) -> bool:
    """A failure that repeats on every attempt, so retrying it only wastes minutes.

    Covers a named local path (permissions, a full disk), a filesystem error
    without a filename (``_FILESYSTEM_ERRNOS``), OpenAI rejecting the request
    or the credentials, and ffmpeg failing to encode the segment. None of
    these says the provider is struggling, so none counts toward a cooldown.
    Network failures are OSErrors too (aiohttp's connection errors, timeouts,
    resets) but name no file and carry socket errnos, so they stay retried
    with the provider's own transient errors.
    """
    if isinstance(exc, OSError):
        return exc.filename is not None or exc.errno in _FILESYSTEM_ERRNOS
    return isinstance(exc, (*_OPENAI_PERMANENT, CouldntEncodeError))


def _synthesize_segment(
    work: SegmentWork,
    renderer: SegmentRenderer,
    output_dir: Path,
    max_attempts: int = 3,
    stop: threading.Event | None = None,
) -> SynthesisResult:
    """Synthesize audio for a single segment. Thread-safe worker function.

    Args:
        work: SegmentWork containing segment and sentences
        renderer: SegmentRenderer instance
        output_dir: Output directory for audio files
        max_attempts: Maximum number of retry attempts
        stop: Set when a cooldown begins. Cancelling a future cannot stop a
            running worker, which kept retrying against the provider throughout
            the cooldown; the worker checks it before every attempt and the
            renderer before every sentence instead.

    Returns:
        SynthesisResult with audio_path/duration or error
    """
    segment_id = work.segment.segment_id
    last_error: Exception | None = None

    # Get text preview from first sentence (for dashboard display)
    text_preview = " ".join(work.sentences[:1]) if work.sentences else ""

    for attempt in range(1, max_attempts + 1):
        if stop is not None and stop.is_set():
            return SynthesisResult(
                segment_id=segment_id, attempts=attempt - 1, text_preview=text_preview,
                deferred=True,
            )
        try:
            audio_path, duration = renderer.render_segment(
                segment_id, work.sentences, output_dir, stop=stop
            )
            return SynthesisResult(
                segment_id=segment_id,
                audio_path=audio_path,
                duration=duration,
                attempts=attempt,
                text_preview=text_preview,
            )
        except RenderStoppedError:
            return SynthesisResult(
                segment_id=segment_id, attempts=attempt, text_preview=text_preview,
                deferred=True,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_permanent_error(exc):
                return SynthesisResult(
                    segment_id=segment_id, error=exc, attempts=attempt,
                    text_preview=text_preview, permanent=True,
                )
            if attempt < max_attempts:
                # Wait before retry, waking at once if a cooldown begins.
                if stop is not None:
                    stop.wait(RETRY_DELAY_SECONDS)
                else:
                    time.sleep(RETRY_DELAY_SECONDS)

    # All attempts failed
    return SynthesisResult(
        segment_id=segment_id,
        error=last_error,
        attempts=max_attempts,
        text_preview=text_preview,
    )


def _text_digest(sentences: list[str]) -> str:
    """Digest of exactly what the renderer speaks for a segment."""
    return hashlib.sha256("\n".join(sentences).encode("utf-8")).hexdigest()


class SegmentWork:
    __slots__ = ("segment", "sentences", "text_sha256")

    def __init__(self, segment: Segment, sentences: list[str]) -> None:
        self.segment = segment
        self.sentences = sentences
        self.text_sha256 = _text_digest(sentences)


class AudiobookRunner:
    def __init__(
        self,
        settings: AppSettings,
        input_epub: Path,
        voice: str,
        language: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        cover_path: Path | None = None,
        cover_only: bool = False,
        tts_provider: str | None = None,
        tts_model: str | None = None,
        tts_speed: float | None = None,
    ) -> None:
        self.settings = settings
        self.input_epub = input_epub
        self.voice = voice
        self.language = language
        self.rate = rate
        self.volume = volume
        self.cover_path = cover_path
        self.cover_only = cover_only
        # TTS provider settings (from config or parameters)
        self.tts_provider = tts_provider or settings.audiobook_tts_provider
        self.tts_model = tts_model or settings.audiobook_tts_model
        if self.tts_provider == "openai" and not self.tts_model:
            self.tts_model = DEFAULT_OPENAI_MODEL
        self.tts_speed = tts_speed if tts_speed is not None else settings.audiobook_tts_speed
        # Provider-specific output directories
        provider_suffix = "edgetts" if self.tts_provider == "edge" else "openaitts"
        self.output_root = settings.work_dir / f"audiobook@{provider_suffix}"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.segment_audio_dir = self.output_root / "segments"
        self.segment_audio_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output_root / "audio_state.json"
        # Open EPUB reader for footnote filtering during re-extraction
        from epub_io.reader import EpubReader
        self.epub_reader = EpubReader(input_epub, settings)

    def _load_translation_state(self):
        if self.settings.state_file.exists():
            return load_translation_state(self.settings.state_file)
        return None

    def _included_segments(self, *, announce: bool = False) -> list[Segment]:
        """Segments this audiobook actually covers, after inclusion/skip filtering.

        Single source of truth for "which segments belong to this audiobook".
        Cover-only validation used to re-scan every stored segment instead, so
        deliberately excluded segments were reported as missing audio and blocked
        the cover rebuild.
        """
        segments_doc = load_segments(self.settings.segments_file)
        original_count = len(segments_doc.segments)

        segments = audiobook_segments(self.settings, segments_doc.segments)

        filtered_count = original_count - len(segments)
        if announce and filtered_count > 0:
            filter_type = (
                "inclusion list" if self.settings.audiobook_files is not None else "skip rules"
            )
            console.print(
                f"[cyan]Filtered {filtered_count} segments from {original_count} "
                f"based on {filter_type}[/cyan]"
            )
        return segments

    def _segments_to_process(self, included: list[Segment]) -> list[SegmentWork]:
        translation_state = self._load_translation_state()
        translation_skips = set()
        if translation_state:
            translation_skips = {
                seg_id
                for seg_id, record in translation_state.segments.items()
                if record.status == SegmentStatus.SKIPPED
            }
        audio_state = load_audio_state(self.state_path)
        ordered_segments = sorted(
            included,
            key=lambda seg: (seg.metadata.spine_index, seg.metadata.order_in_file),
        )
        works: list[SegmentWork] = []
        for segment in ordered_segments:
            if segment.segment_id in translation_skips:
                mark_status(
                    self.state_path,
                    segment.segment_id,
                    AudioSegmentStatus.SKIPPED,
                    last_error="translation skip",
                )
                continue
            text = segment_to_text(segment, reader=self.epub_reader)
            if not text:
                mark_status(
                    self.state_path,
                    segment.segment_id,
                    AudioSegmentStatus.SKIPPED,
                    last_error="empty or non-text content",
                )
                continue
            sentences = split_sentences(text, language=self.language)
            if not sentences:
                mark_status(
                    self.state_path,
                    segment.segment_id,
                    AudioSegmentStatus.SKIPPED,
                    last_error="could not split sentences",
                )
                continue
            previous = audio_state.segments.get(segment.segment_id)
            if previous is not None and previous.status == AudioSegmentStatus.SKIPPED:
                # Skipped by an earlier run but speakable now; left SKIPPED, the
                # queue passed over it and the book silently lacked it.
                mark_status(
                    self.state_path,
                    segment.segment_id,
                    AudioSegmentStatus.PENDING,
                    last_error=None,
                )
            works.append(SegmentWork(segment, sentences))

        # Segment IDs are positions, so completed audio is reused only while the
        # text it was rendered from is unchanged.
        reset = apply_text_digests(
            self.state_path, {work.segment.segment_id: work.text_sha256 for work in works}
        )
        if reset:
            console.print(
                f"[yellow]Text changed for {len(reset)} segments; "
                "re-synthesising their audio.[/yellow]"
            )
        return works

    def _resolve_cover(self, state: AudioStateDocument) -> None:
        if self.cover_path is None and state.session.cover_path:
            self.cover_path = state.session.cover_path

        if self.cover_path:
            cover_fs = Path(self.cover_path)
            if cover_fs.exists():
                self.cover_path = cover_fs
            else:
                console.print(
                    f"[yellow]Cover path {escape(str(cover_fs))} is missing; "
                    "falling back to automatic selection.[/yellow]"
                )
                self.cover_path = None
                state.session.cover_path = None
                save_state(state, self.state_path)

    def _assemble(self, state: AudioStateDocument, included_ids: set[str]) -> Path:
        final_path = assemble_audiobook(
            settings=self.settings,
            input_epub=self.input_epub,
            session=state.session,
            state_path=self.state_path,
            output_root=self.output_root,
            included_segment_ids=included_ids,
        )
        if final_path is None:
            # Printing a note and returning let the command exit 0 with no book.
            raise AudiobookIncompleteError(
                "No completed segment audio to assemble; no audiobook was written."
            )
        console.print(f"[green]Final audiobook written to {escape(str(final_path))}[/green]")
        return final_path

    def _wait_out_cooldown(self, until: datetime, on_tick=None) -> None:
        """Pause provider requests until ``until``, then clear the cooldown.

        Announced on the console as well as the dashboard: without a terminal
        the dashboard draws nothing, and a 30-minute silence looked like a hang.
        """
        until = _as_utc(until)
        console.print(
            f"[yellow]{COOLDOWN_AFTER_FAILURES} consecutive failures; pausing synthesis "
            f"until {until.astimezone():%H:%M:%S}.[/yellow]"
        )
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        while remaining > 0:
            if on_tick is not None:
                on_tick(remaining)
            time.sleep(min(5, remaining))
            remaining = (until - datetime.now(timezone.utc)).total_seconds()
        set_cooldown(self.state_path, None)
        set_consecutive_failures(self.state_path, 0)
        console.print("[cyan]Cooldown over; resuming synthesis.[/cyan]")

    def run(self) -> None:
        # Two runs on one workspace overwrote each other's segments, chapters
        # and final file.
        with exclusive_run(self.state_path):
            if self.cover_only:
                self._run_cover_only()
            else:
                self._run_synthesis()

    def _run_cover_only(self) -> None:
        if not self.state_path.exists():
            raise AudiobookIncompleteError(
                f"No audiobook to rebuild in {self.output_root}; "
                "run without --cover-only first."
            )
        # Only the cover changes. Passing this run's audio settings through
        # ensure_state discarded completed audio whenever they differed, and
        # then refused the rebuild for lack of it.
        state = load_audio_state(self.state_path)
        if self.cover_path is not None and state.session.cover_path != self.cover_path:
            state.session.cover_path = self.cover_path
            save_state(state, self.state_path)
        self._resolve_cover(state)

        included = self._included_segments()
        missing: list[str] = []
        for segment in included:
            seg_state = state.segments.get(segment.segment_id)
            # Deliberately skipped segments (tables, figures, translation skips)
            # have no audio by design.
            if seg_state is not None and seg_state.status == AudioSegmentStatus.SKIPPED:
                continue
            if not _has_audio(seg_state):
                missing.append(segment.segment_id)
        if missing:
            raise AudiobookIncompleteError(
                f"Cannot assemble cover-only audiobook; {len(missing)} segments are "
                "incomplete or missing audio files. Re-run without --cover-only to "
                "resynthesise them."
            )
        self._assemble(state, {seg.segment_id for seg in included})

    def _run_synthesis(self) -> None:
        state = ensure_state(
            self.state_path,
            self.segment_audio_dir,
            self.voice,
            language=self.language,
            cover_path=self.cover_path,
            tts_provider=self.tts_provider,
            tts_model=self.tts_model,
            tts_speed=self.tts_speed,
            rate=self.rate,
            volume=self.volume,
        )
        self._resolve_cover(state)
        renderer = self._make_renderer(state)

        # One selection serves synthesis and assembly; reading it twice let the
        # two disagree if the segments file changed in between.
        included = self._included_segments(announce=True)
        works = self._segments_to_process(included)
        work_map = {work.segment.segment_id: work for work in works}
        if not work_map:
            # Returning normally let the command exit 0 with no book.
            raise AudiobookIncompleteError(
                "None of the selected segments has text to speak; no audiobook was "
                "written. Check the audiobook inclusion and skip settings."
            )

        reset_count = len(reset_error_segments(self.state_path))
        if reset_count:
            console.print(
                f"[yellow]Retrying {reset_count} segments left in error state "
                "from a previous run.[/yellow]"
            )

        try:
            self._synthesise(work_map, renderer)

            final_state = load_audio_state(self.state_path)
            outstanding = [
                seg_id
                for seg_id in work_map
                if (
                    seg_id in final_state.segments
                    and final_state.segments[seg_id].status == AudioSegmentStatus.ERROR
                )
            ]
            if outstanding:
                # Returning normally let the command exit 0 without a book.
                raise AudiobookIncompleteError(
                    f"{len(outstanding)} segments remain in error state; "
                    "rerun the command to retry them."
                )

            self._assemble(state, {seg.segment_id for seg in included})

        except KeyboardInterrupt:
            console.print(
                "[yellow]Progress saved; resume later with tepub audiobook.[/yellow]"
            )
            # Exiting 0 here told any calling script that the book was finished.
            raise

    def _make_renderer(self, state: AudioStateDocument) -> SegmentRenderer:
        # Render with the session's settings, not this invocation's: an omitted
        # --rate or --volume left the stored value in the session but rendered
        # default prosody, mixing two voices in one book.
        session = state.session
        engine = create_tts_engine(
            provider=session.tts_provider,
            voice=session.voice,
            rate=session.rate,
            volume=session.volume,
            model=session.tts_model or DEFAULT_OPENAI_MODEL,
            speed=session.tts_speed,
        )
        return SegmentRenderer(
            engine, session.sentence_pause_range, epub_reader=self.epub_reader
        )

    def _synthesise(self, work_map: dict[str, SegmentWork], renderer: SegmentRenderer) -> None:
        """Run passes over the segments still lacking audio until none is left,
        or a pass makes no progress at all."""
        audio_state = load_audio_state(self.state_path)
        progress = _SynthesisProgress(
            len(work_map), *_tally(audio_state, work_map),
            max_workers=self.settings.audiobook_workers,
        )
        with progress.shown():
            # A cooldown persisted by an earlier run still applies; restarting
            # used to resume provider requests at once.
            if audio_state.cooldown_until is not None:
                if _as_utc(audio_state.cooldown_until) > datetime.now(timezone.utc):
                    self._cooldown(audio_state.cooldown_until, progress)
                else:
                    set_cooldown(self.state_path, None)

            # A rejected key or a full disk failed again on every later pass
            # that another segment's success started; the next run retries them.
            given_up: set[str] = set()
            while True:
                audio_state = load_audio_state(self.state_path)
                pending_queue = [
                    seg_id
                    for seg_id in work_map
                    if seg_id not in given_up
                    and _needs_audio(audio_state.segments.get(seg_id))
                ]
                if not pending_queue:
                    break

                # Recompute the dashboard counters from state rather than carrying
                # the previous pass's arithmetic forward, with the same "done"
                # test the queue uses: a COMPLETED segment whose file is gone is
                # queued, so counting it as completed too ran progress past total.
                progress.recount(audio_state, work_map, len(pending_queue))

                outcome = self._run_pass(pending_queue, work_map, renderer, progress)

                if outcome.cooldown_until is not None:
                    self._cooldown(outcome.cooldown_until, progress)

                given_up.update(outcome.permanent)
                if outcome.failures:
                    if outcome.successes == 0:
                        break
                    progress.reset_previews()
                    retry = [s for s in outcome.failures if s not in given_up]
                    if retry:  # an empty list would reset every error segment
                        reset_error_segments(self.state_path, retry)

    def _cooldown(self, until: datetime, progress: _SynthesisProgress) -> None:
        progress.set_cooldown(True)
        self._wait_out_cooldown(until, progress.show_cooldown)
        progress.set_cooldown(False)

    def _run_pass(
        self,
        pending_queue: list[str],
        work_map: dict[str, SegmentWork],
        renderer: SegmentRenderer,
        progress: _SynthesisProgress,
    ) -> _PassOutcome:
        """Synthesise ``pending_queue`` in parallel, saving each result as it lands."""
        outcome = _PassOutcome()
        stop = threading.Event()
        executor = ThreadPoolExecutor(max_workers=progress.max_workers)
        try:
            future_to_seg_id = {}
            for seg_id in pending_queue:
                if _has_audio(get_or_create_segment(self.state_path, seg_id)):
                    continue
                future = executor.submit(
                    _synthesize_segment,
                    work_map[seg_id],
                    renderer,
                    self.segment_audio_dir,
                    max_attempts=3,
                    stop=stop,
                )
                future_to_seg_id[future] = seg_id
            progress.running = set(future_to_seg_id)

            for future in as_completed(future_to_seg_id):
                progress.running.discard(future)
                seg_id = future_to_seg_id[future]
                if future.cancelled():
                    # Cancelled when cooldown began; the segment stays
                    # pending and is picked up by the next pass.
                    continue
                result = future.result()
                if result.deferred:
                    continue
                if result.error:
                    outcome.failures.append(seg_id)
                    if result.permanent:
                        outcome.permanent.append(seg_id)
                    if self._record_failure(seg_id, result) and outcome.cooldown_until is None:
                        # Stop queued work and tell running workers not to
                        # start another request. Results still arriving are
                        # saved as this loop drains; the wait happens after
                        # it, so nothing finished is held unsaved.
                        stop.set()
                        for queued in future_to_seg_id:
                            if not queued.done():
                                queued.cancel()
                        outcome.cooldown_until = datetime.now(timezone.utc) + COOLDOWN
                        set_cooldown(self.state_path, outcome.cooldown_until)
                else:
                    self._record_success(seg_id, result, work_map[seg_id])
                    outcome.successes += 1
                progress.record(result)

        except KeyboardInterrupt:
            # Covers submission as well as collection: an interrupt while
            # submitting used to wait for every running worker.
            console.print("\n[yellow]Interrupted by user. Saving progress...[/yellow]")
            stop.set()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)
        progress.running = set()
        return outcome

    def _record_failure(self, seg_id: str, result: SynthesisResult) -> bool:
        """Save a failed segment; True once failures call for a cooldown."""
        mark_status(
            self.state_path,
            seg_id,
            AudioSegmentStatus.ERROR,
            attempts=result.attempts,
            last_error=str(result.error),
        )
        if result.permanent:
            # A full disk or a rejected API key says nothing about the
            # provider's load; pausing requests for half an hour fixes neither.
            return False
        consecutive = load_audio_state(self.state_path).consecutive_failures + 1
        set_consecutive_failures(self.state_path, consecutive)
        return consecutive >= COOLDOWN_AFTER_FAILURES

    def _record_success(self, seg_id: str, result: SynthesisResult, work: SegmentWork) -> None:
        mark_status(
            self.state_path,
            seg_id,
            AudioSegmentStatus.COMPLETED,
            audio_path=result.audio_path,
            duration_seconds=result.duration,
            attempts=result.attempts,
            last_error=None,
            text_sha256=work.text_sha256,
        )
        set_consecutive_failures(self.state_path, 0)


class _PassOutcome:
    """What one synthesis pass achieved."""

    __slots__ = ("successes", "failures", "permanent", "cooldown_until")

    def __init__(self) -> None:
        self.successes = 0
        self.failures: list[str] = []
        # Failed for a reason another attempt in this run cannot fix.
        self.permanent: list[str] = []
        self.cooldown_until: datetime | None = None


class _SynthesisProgress:
    """The counters, previews and cooldown note the dashboard shows.

    Kept apart from the runner so that saving results and drawing them do not
    share one long function's local variables.
    """

    def __init__(
        self, total: int, completed: int, skipped: int, errors: int, *, max_workers: int
    ) -> None:
        self.total = total
        self.completed = completed
        self.skipped = skipped
        self.errors = errors
        self.pending = total - completed - skipped
        self.max_workers = max_workers
        # Fixed-size array to prevent height changes (always max_workers slots)
        self.preview_lines = [Text("")] * max_workers
        self.preview_index = 0
        self.running: set = set()
        self.in_cooldown = False
        self.cooldown_remaining = ""
        self.bar = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            auto_refresh=False,
        )
        self.task_id = self.bar.add_task("Synthesis", total=total, completed=completed)
        self.plain = PlainProgress("Synthesised", total, console=console)
        self._live = None

    def _renderable(self) -> Group:
        panel = _build_audiobook_dashboard(
            total_segments=self.total,
            completed_segments=self.completed,
            skipped_segments=self.skipped,
            error_segments=self.errors,
            pending_segments=self.pending,
            preview_lines=self.preview_lines,
            # Submitted futures include queued ones; only running ones are workers.
            active_workers=sum(1 for future in self.running if future.running()),
            max_workers=self.max_workers,
            in_cooldown=self.in_cooldown,
            cooldown_remaining=self.cooldown_remaining,
        )
        return Group(panel, self.bar)

    @contextmanager
    def shown(self):
        with live_display(
            self._renderable(),
            console=console,
            refresh_per_second=5,
            vertical_overflow="crop",  # Prevent height expansion causing scrolling
        ) as live:
            self._live = live
            try:
                yield self
            finally:
                self._live = None

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._renderable())

    def recount(self, audio_state: AudioStateDocument, seg_ids, pending: int) -> None:
        self.completed, self.skipped, self.errors = _tally(audio_state, seg_ids)
        self.pending = pending
        self.bar.update(self.task_id, completed=self.completed)

    def record(self, result: SynthesisResult) -> None:
        if result.error:
            self.preview_lines[self.preview_index] = _preview(str(result.error), "red")
            self.errors += 1
        else:
            self.preview_lines[self.preview_index] = _preview(result.text_preview, "green")
            self.completed += 1
            # Only verified audio advances progress; failures used to be
            # reported as synthesised.
            self.bar.advance(self.task_id)
        self.pending -= 1
        # Round-robin through preview slots
        self.preview_index = (self.preview_index + 1) % self.max_workers
        self._refresh()
        self.plain.report(self.completed)

    def reset_previews(self) -> None:
        self.preview_lines = [Text("waiting…")] * self.max_workers
        self.preview_index = 0

    def set_cooldown(self, active: bool) -> None:
        self.in_cooldown = active
        if active:
            self._refresh()
        else:
            self.cooldown_remaining = ""

    def show_cooldown(self, remaining: float) -> None:
        self.cooldown_remaining = f"{int(remaining // 60)}m {int(remaining % 60)}s"
        self._refresh()


def run_audiobook(
    settings: AppSettings,
    input_epub: Path,
    voice: str,
    language: str | None = None,
    rate: str | None = None,
    volume: str | None = None,
    cover_path: Path | None = None,
    cover_only: bool = False,
    tts_provider: str | None = None,
    tts_model: str | None = None,
    tts_speed: float | None = None,
) -> None:
    runner = AudiobookRunner(
        settings=settings,
        input_epub=input_epub,
        voice=voice,
        language=language,
        rate=rate,
        volume=volume,
        cover_path=cover_path,
        cover_only=cover_only,
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_speed=tts_speed,
    )
    runner.run()
