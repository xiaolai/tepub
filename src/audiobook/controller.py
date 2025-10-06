from __future__ import annotations

import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import AppSettings
from console_singleton import get_console
from state.models import Segment, SegmentStatus
from state.store import load_segments
from state.store import load_state as load_translation_state

from .assembly import assemble_audiobook
from .models import AudioSegmentStatus
from .preprocess import segment_to_text, split_sentences
from .renderer import SegmentRenderer
from .state import (
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

console = get_console()


def _build_audiobook_dashboard(
    *,
    total_segments: int,
    completed_segments: int,
    skipped_segments: int,
    error_segments: int,
    pending_segments: int,
    preview_lines: list[str],
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
        stats.add_row("workers", f"single worker")

    # Always show cooldown row (fixed height)
    if in_cooldown:
        stats.add_row("cooldown", f"⏸  waiting {cooldown_remaining} (3 consecutive fails)")
    else:
        stats.add_row("cooldown", "")  # Empty row to maintain height

    # ALWAYS show exactly max_workers preview lines (fixed height)
    for i in range(max_workers):
        label = f"w{i+1}" if max_workers > 1 else "current"
        # Truncate preview text to 60 chars to prevent wrapping
        text = preview_lines[i] if i < len(preview_lines) else ""
        if text and len(text) > 60:
            text = text[:59] + "…"
        stats.add_row(label, text or "")

    return Panel(stats, border_style="magenta", title="Dashboard", padding=(1, 1))


def _truncate_text(text: str, max_length: int = 80) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


class SynthesisResult:
    """Result of synthesizing audio for a single segment."""

    __slots__ = ("segment_id", "audio_path", "duration", "error", "attempts", "text_preview")

    def __init__(
        self,
        segment_id: str,
        audio_path: Path | None = None,
        duration: float | None = None,
        error: Exception | None = None,
        attempts: int = 0,
        text_preview: str = "",
    ):
        self.segment_id = segment_id
        self.audio_path = audio_path
        self.duration = duration
        self.error = error
        self.attempts = attempts
        self.text_preview = text_preview


def _synthesize_segment(
    work: "SegmentWork",
    renderer: SegmentRenderer,
    output_dir: Path,
    max_attempts: int = 3,
) -> SynthesisResult:
    """Synthesize audio for a single segment. Thread-safe worker function.

    Args:
        work: SegmentWork containing segment and sentences
        renderer: SegmentRenderer instance
        output_dir: Output directory for audio files
        max_attempts: Maximum number of retry attempts

    Returns:
        SynthesisResult with audio_path/duration or error
    """
    segment_id = work.segment.segment_id
    last_error: Exception | None = None

    # Get text preview from first sentence (for dashboard display)
    text_preview = " ".join(work.sentences[:1]) if work.sentences else ""

    for attempt in range(1, max_attempts + 1):
        try:
            audio_path, duration = renderer.render_segment(
                segment_id, work.sentences, output_dir
            )
            return SynthesisResult(
                segment_id=segment_id,
                audio_path=audio_path,
                duration=duration,
                attempts=attempt,
                text_preview=text_preview,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < max_attempts:
                time.sleep(60)  # Wait before retry

    # All attempts failed
    return SynthesisResult(
        segment_id=segment_id,
        error=last_error,
        attempts=max_attempts,
        text_preview=text_preview,
    )


class SegmentWork:
    __slots__ = ("segment", "sentences")

    def __init__(self, segment: Segment, sentences: list[str]) -> None:
        self.segment = segment
        self.sentences = sentences


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

    def _segments_to_process(self) -> list[SegmentWork]:
        segments_doc = load_segments(self.settings.segments_file)

        # Filter segments based on audiobook_files inclusion list or skip metadata
        original_count = len(segments_doc.segments)
        if self.settings.audiobook_files is not None:
            # Explicit inclusion list takes precedence
            allowed_files = set(self.settings.audiobook_files)
            segments_doc.segments = [
                seg
                for seg in segments_doc.segments
                if seg.file_path.as_posix() in allowed_files
            ]
        else:
            # No inclusion list: filter out segments with skip metadata
            segments_doc.segments = [
                seg
                for seg in segments_doc.segments
                if seg.skip_reason is None
            ]

        filtered_count = original_count - len(segments_doc.segments)
        if filtered_count > 0:
            filter_type = "inclusion list" if self.settings.audiobook_files is not None else "skip rules"
            console.print(
                f"[cyan]Filtered {filtered_count} segments from {original_count} "
                f"based on {filter_type}[/cyan]"
            )

        translation_state = self._load_translation_state()
        translation_skips = set()
        if translation_state:
            translation_skips = {
                seg_id
                for seg_id, record in translation_state.segments.items()
                if record.status == SegmentStatus.SKIPPED
            }
        ordered_segments = sorted(
            segments_doc.segments,
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
            sentences = split_sentences(text)
            if not sentences:
                mark_status(
                    self.state_path,
                    segment.segment_id,
                    AudioSegmentStatus.SKIPPED,
                    last_error="could not split sentences",
                )
                continue
            works.append(SegmentWork(segment, sentences))
        return works

    def run(self) -> None:
        state = ensure_state(
            self.state_path,
            self.segment_audio_dir,
            self.voice,
            language=self.language,
            cover_path=self.cover_path,
            tts_provider=self.tts_provider,
            tts_model=self.tts_model,
            tts_speed=self.tts_speed,
        )

        if self.cover_path is None and state.session.cover_path:
            self.cover_path = state.session.cover_path

        if self.cover_path:
            cover_fs = Path(self.cover_path)
            if cover_fs.exists():
                self.cover_path = cover_fs
            else:
                console.print(
                    f"[yellow]Cover path {cover_fs} is missing; falling back to automatic selection.[/yellow]"
                )
                self.cover_path = None
                state.session.cover_path = None
                save_state(state, self.state_path)

        if self.cover_only:
            segments_doc = load_segments(self.settings.segments_file)
            audio_state = load_audio_state(self.state_path)
            missing: list[str] = []
            for segment in segments_doc.segments:
                seg_state = audio_state.segments.get(segment.segment_id)
                if not seg_state or seg_state.status != AudioSegmentStatus.COMPLETED:
                    missing.append(segment.segment_id)
                    continue
                if not seg_state.audio_path or not Path(seg_state.audio_path).exists():
                    missing.append(segment.segment_id)
            if missing:
                console.print(
                    f"[red]Cannot assemble cover-only audiobook; {len(missing)} segments are incomplete or missing audio files.[/red]"
                )
                console.print(
                    "[yellow]Re-run without --cover-only to resynthesise missing segments.[/yellow]"
                )
                return
            final_path = assemble_audiobook(
                settings=self.settings,
                input_epub=self.input_epub,
                session=state.session,
                state_path=self.state_path,
                output_root=self.output_root,
            )
            if final_path:
                console.print(f"[green]Final audiobook written to {final_path}[/green]")
            else:
                console.print("[yellow]No completed segments found; nothing to assemble.[/yellow]")
            return

        # Create TTS engine based on provider
        engine = create_tts_engine(
            provider=self.tts_provider,
            voice=self.voice,
            rate=self.rate,
            volume=self.volume,
            model=self.tts_model,
            speed=self.tts_speed,
        )
        renderer = SegmentRenderer(engine, state.session.sentence_pause_range, epub_reader=self.epub_reader)

        works = self._segments_to_process()
        work_map = {work.segment.segment_id: work for work in works}
        total_segments = len(work_map)
        if total_segments == 0:
            console.print("[green]No segments require audio synthesis.[/green]")
            return

        reset_count = len(reset_error_segments(self.state_path))
        if reset_count:
            console.print(
                f"[yellow]Retrying {reset_count} segments left in error state from a previous run.[/yellow]"
            )

        # Track statistics for dashboard
        audio_state = load_audio_state(self.state_path)
        completed_segments = sum(
            1
            for seg_id in work_map
            if (
                seg_id in audio_state.segments
                and audio_state.segments[seg_id].status == AudioSegmentStatus.COMPLETED
            )
        )
        skipped_segments = sum(
            1
            for seg_id in work_map
            if (
                seg_id in audio_state.segments
                and audio_state.segments[seg_id].status == AudioSegmentStatus.SKIPPED
            )
        )
        error_segments = sum(
            1
            for seg_id in work_map
            if (
                seg_id in audio_state.segments
                and audio_state.segments[seg_id].status == AudioSegmentStatus.ERROR
            )
        )
        pending_segments = total_segments - completed_segments - skipped_segments

        max_workers = self.settings.audiobook_workers
        # Fixed-size array to prevent height changes (always max_workers slots)
        preview_lines = [""] * max_workers
        preview_index = 0
        active_workers = 0
        in_cooldown = False
        cooldown_remaining = ""

        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            auto_refresh=False,
        )
        task_id = progress.add_task("Synthesis", total=total_segments, completed=completed_segments)
        cooldown_task: TaskID | None = None

        def render_panel() -> Panel:
            return _build_audiobook_dashboard(
                total_segments=total_segments,
                completed_segments=completed_segments,
                skipped_segments=skipped_segments,
                error_segments=error_segments,
                pending_segments=pending_segments,
                preview_lines=preview_lines,
                active_workers=active_workers,
                max_workers=max_workers,
                in_cooldown=in_cooldown,
                cooldown_remaining=cooldown_remaining,
            )

        try:
            with Live(
                Group(render_panel(), progress),
                console=console,
                refresh_per_second=5,
                vertical_overflow="crop",  # Prevent height expansion causing scrolling
            ) as live:
                while True:
                    audio_state = load_audio_state(self.state_path)
                    pending_queue: list[str] = []
                    for seg_id in work_map:
                        seg_state = audio_state.segments.get(seg_id)
                        if seg_state and seg_state.status in (
                            AudioSegmentStatus.COMPLETED,
                            AudioSegmentStatus.SKIPPED,
                        ):
                            continue
                        pending_queue.append(seg_id)

                    if not pending_queue:
                        break

                    pass_successes = 0
                    pass_failures: list[str] = []
                    interrupted = False

                    # Use parallel synthesis with ThreadPoolExecutor
                    executor = ThreadPoolExecutor(max_workers=max_workers)
                    try:
                        # Submit all pending segments
                        future_to_seg_id = {}
                        for seg_id in pending_queue:
                            work = work_map[seg_id]
                            seg_state = get_or_create_segment(self.state_path, seg_id)
                            if (
                                seg_state.status == AudioSegmentStatus.COMPLETED
                                and seg_state.audio_path
                                and Path(seg_state.audio_path).exists()
                            ):
                                continue

                            future = executor.submit(
                                _synthesize_segment,
                                work,
                                renderer,
                                self.segment_audio_dir,
                                max_attempts=3,
                            )
                            future_to_seg_id[future] = seg_id
                            active_workers += 1

                        # Process results as they complete
                        try:
                            for future in as_completed(future_to_seg_id):
                                active_workers -= 1
                                seg_id = future_to_seg_id[future]
                                result = future.result()

                                if result.error:
                                    mark_status(
                                        self.state_path,
                                        seg_id,
                                        AudioSegmentStatus.ERROR,
                                        attempts=result.attempts,
                                        last_error=str(result.error),
                                    )
                                    error_msg = _truncate_text(str(result.error))
                                    preview_lines[preview_index] = f"[red]{error_msg}[/red]"
                                    error_segments += 1
                                    pending_segments -= 1
                                    pass_failures.append(seg_id)
                                    audio_state = load_audio_state(self.state_path)
                                    consecutive = audio_state.consecutive_failures + 1
                                    set_consecutive_failures(self.state_path, consecutive)
                                    if consecutive >= 3:
                                        in_cooldown = True
                                        cooldown_until = datetime.utcnow() + timedelta(minutes=30)
                                        set_cooldown(self.state_path, cooldown_until)
                                        duration = 30 * 60
                                        remaining = (cooldown_until - datetime.utcnow()).total_seconds()
                                        while remaining > 0:
                                            mins = int(remaining // 60)
                                            secs = int(remaining % 60)
                                            cooldown_remaining = f"{mins}m {secs}s"
                                            live.update(Group(render_panel(), progress))
                                            sleep_for = min(5, remaining)
                                            time.sleep(sleep_for)
                                            remaining = (cooldown_until - datetime.utcnow()).total_seconds()
                                        set_cooldown(self.state_path, None)
                                        set_consecutive_failures(self.state_path, 0)
                                        in_cooldown = False
                                        cooldown_remaining = ""
                                else:
                                    mark_status(
                                        self.state_path,
                                        seg_id,
                                        AudioSegmentStatus.COMPLETED,
                                        audio_path=result.audio_path,
                                        duration_seconds=result.duration,
                                        attempts=result.attempts,
                                        last_error=None,
                                    )
                                    set_consecutive_failures(self.state_path, 0)
                                    text = _truncate_text(result.text_preview)
                                    preview_lines[preview_index] = f"[green]{text}[/green]"
                                    completed_segments += 1
                                    pending_segments -= 1
                                    pass_successes += 1

                                # Round-robin through preview slots
                                preview_index = (preview_index + 1) % max_workers

                                # Update live dashboard
                                progress.advance(task_id)
                                live.update(Group(render_panel(), progress))

                        except KeyboardInterrupt:
                            interrupted = True
                            console.print("\n[yellow]Interrupted by user. Saving progress...[/yellow]")
                            # Force immediate shutdown without waiting
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise
                    finally:
                        # Clean shutdown for normal completion only
                        if not interrupted:
                            executor.shutdown(wait=True)

                    if pass_failures:
                        if pass_successes == 0:
                            break
                        # Reset preview lines to "waiting…" for next pass
                        preview_lines = ["waiting…"] * max_workers
                        preview_index = 0
                        reset_error_segments(self.state_path, pass_failures)
                        continue

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
                console.print(
                    f"[red]{len(outstanding)} segments remain in error state; rerun the command to retry them.[/red]"
                )
                return

            final_path = assemble_audiobook(
                settings=self.settings,
                input_epub=self.input_epub,
                session=state.session,
                state_path=self.state_path,
                output_root=self.output_root,
            )
            if final_path:
                console.print(f"[green]Final audiobook written to {final_path}[/green]")

        except KeyboardInterrupt:
            console.print(
                "[yellow]Progress saved; resume later with tepub audiobook.[/yellow]"
            )
            sys.exit(0)


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
