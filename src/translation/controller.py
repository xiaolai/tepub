from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import AppSettings
from console_singleton import get_console
from logging_utils.logger import get_logger
from state.models import SegmentStatus
from state.store import (
    ensure_state,
    load_segments,
    load_state,
    mark_status,
    reset_error_segments,
    save_state,
    set_consecutive_failures,
    set_cooldown,
)
from translation.languages import describe_language
from translation.polish import polish_if_chinese, polish_translation, target_is_chinese
from translation.providers import ProviderError, ProviderFatalError, create_provider

from .prefilter import should_auto_copy


def _build_dashboard_panel(
    *,
    total_files: int,
    skipped_files: int,
    completed_files: int,
    total_segments: int,
    completed_segments: int,
    pending_segments: int,
    preview_lines: list[str],
    progress_renderable,
    active_workers: int = 0,
    max_workers: int = 1,
    in_cooldown: bool = False,
    cooldown_remaining: str = "",
) -> Panel:
    stats = Table.grid(padding=(1, 1))
    stats.add_column(style="bold cyan", justify="left")
    stats.add_column(justify="left", overflow="fold")
    stats.add_row(
        "files",
        f"total {total_files}, skipped {skipped_files}, completed {completed_files}",
    )
    stats.add_row(
        "segments",
        f"total {total_segments}, completed {completed_segments}, pending {pending_segments}",
    )
    if max_workers > 1:
        stats.add_row("workers", f"active {active_workers}/{max_workers}")

    if in_cooldown:
        stats.add_row("cooldown", f"⏸  waiting {cooldown_remaining} (3 consecutive fails)")

    # Show preview lines (one per worker slot)
    for i, line in enumerate(preview_lines):
        label = f"w{i+1}" if max_workers > 1 else "current"
        stats.add_row(label, line or "…")

    return Panel(stats, border_style="magenta", title="Dashboard", padding=(1, 1))


def _strip_tags(text: str) -> str:
    from lxml import html

    try:
        wrapper = html.fromstring(f"<div>{text}</div>")
        cleaned = " ".join(part.strip() for part in wrapper.itertext())
        return " ".join(cleaned.split())
    except Exception:  # pragma: no cover - fallback for malformed html
        return text


def _truncate_text(text: str, max_length: int = 80) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


logger = get_logger(__name__)
console = get_console()


class TranslationResult:
    """Result of translating a single segment."""

    __slots__ = ("segment_id", "translation", "error", "is_auto_copy", "provider_name", "model_name")

    def __init__(
        self,
        segment_id: str,
        translation: str | None = None,
        error: Exception | None = None,
        is_auto_copy: bool = False,
        provider_name: str | None = None,
        model_name: str | None = None,
    ):
        self.segment_id = segment_id
        self.translation = translation
        self.error = error
        self.is_auto_copy = is_auto_copy
        self.provider_name = provider_name
        self.model_name = model_name


def _translate_segment(
    segment,
    provider,
    source_language: str,
    target_language: str,
) -> TranslationResult:
    """Translate a single segment. Thread-safe worker function.

    Args:
        segment: The segment to translate
        provider: Translation provider instance
        source_language: Source language code
        target_language: Target language code

    Returns:
        TranslationResult with translation or error
    """
    # Check if segment should be auto-copied
    if should_auto_copy(segment):
        return TranslationResult(
            segment_id=segment.segment_id,
            translation=segment.source_content,
            is_auto_copy=True,
            provider_name=None,
            model_name=None,
        )

    # Perform translation
    try:
        translation_text = provider.translate(
            segment,
            source_language=source_language,
            target_language=target_language,
        )
        # Apply polish immediately
        polished_text = polish_translation(translation_text)
        return TranslationResult(
            segment_id=segment.segment_id,
            translation=polished_text,
            provider_name=provider.name,
            model_name=provider.model,
        )
    except (ProviderError, ProviderFatalError) as exc:
        return TranslationResult(
            segment_id=segment.segment_id,
            error=exc,
        )
    except Exception as exc:  # pragma: no cover - unexpected errors
        return TranslationResult(
            segment_id=segment.segment_id,
            error=ProviderError(f"Unexpected error: {exc}"),
        )


def run_translation(
    settings: AppSettings,
    input_epub: Path,
    *,
    source_language: str,
    target_language: str,
) -> None:
    settings.ensure_directories()

    segments_doc = load_segments(settings.segments_file)
    if segments_doc.epub_path != input_epub:
        logger.warning(
            "Segments file was generated for %s but %s was provided.",
            segments_doc.epub_path,
            input_epub,
        )

    # Filter segments based on translation_files inclusion list or skip metadata
    original_count = len(segments_doc.segments)
    if settings.translation_files is not None:
        # Explicit inclusion list takes precedence
        allowed_files = set(settings.translation_files)
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
        filter_type = "inclusion list" if settings.translation_files is not None else "skip rules"
        console.print(
            f"[cyan]Filtered {filtered_count} segments from {original_count} "
            f"based on {filter_type}[/cyan]"
        )

    provider = create_provider(settings.primary_provider)
    state_doc = ensure_state(
        settings.state_file,
        segments_doc.segments,
        provider.name,
        provider.model,
        source_language,
        target_language,
    )

    if state_doc.source_language != source_language or state_doc.target_language != target_language:
        console.print("[yellow]Language preferences changed; resetting translation state.[/yellow]")
        state_doc = ensure_state(
            settings.state_file,
            segments_doc.segments,
            provider.name,
            provider.model,
            source_language,
            target_language,
            force_reset=True,
        )

    console.print(
        f"Translating from {describe_language(source_language)} into {describe_language(target_language)} using {provider.model}"
    )

    total = sum(
        1
        for seg in segments_doc.segments
        if not state_doc.segments.get(seg.segment_id)
        or state_doc.segments.get(seg.segment_id).status != SegmentStatus.COMPLETED
    )
    if total == 0:
        console.print("[green]All segments already translated.[/green]")
        return

    file_totals: Counter[Path] = Counter(seg.file_path for seg in segments_doc.segments)
    file_completed = Counter()
    for seg in segments_doc.segments:
        record = state_doc.segments.get(seg.segment_id)
        if record and record.status == SegmentStatus.COMPLETED:
            file_completed[seg.file_path] += 1

    completed_segments = sum(
        1 for record in state_doc.segments.values() if record.status == SegmentStatus.COMPLETED
    )
    pending_segments = total
    skipped_files = len({doc.file_path for doc in segments_doc.skipped_documents})
    total_files = len(file_totals)

    def completed_files_count() -> int:
        return sum(
            1
            for path, total_required in file_totals.items()
            if file_completed[path] >= total_required
        )

    preview_text = "waiting…"

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        auto_refresh=False,
    )
    task_id = progress.add_task(
        "Translating", total=len(segments_doc.segments), completed=completed_segments
    )

    # Collect pending segments
    pending_segments_list = [
        seg
        for seg in segments_doc.segments
        if not state_doc.segments.get(seg.segment_id)
        or state_doc.segments.get(seg.segment_id).status != SegmentStatus.COMPLETED
    ]

    max_workers = settings.translation_workers
    active_workers = 0

    # Track recent completions (one per worker slot) for display
    preview_lines = ["waiting…"] * max_workers
    preview_index = 0  # Round-robin index for updating preview lines

    # Track cooldown state
    in_cooldown = False
    cooldown_remaining = ""

    def render_panel() -> Panel:
        return _build_dashboard_panel(
            total_files=total_files,
            skipped_files=skipped_files,
            completed_files=completed_files_count(),
            total_segments=len(segments_doc.segments),
            completed_segments=completed_segments,
            pending_segments=pending_segments,
            preview_lines=preview_lines,
            progress_renderable=None,
            active_workers=active_workers,
            max_workers=max_workers,
            in_cooldown=in_cooldown,
            cooldown_remaining=cooldown_remaining,
        )

    with Live(Group(render_panel(), progress), console=console, refresh_per_second=5) as live:
        try:
            while True:
                # Reload state to get pending segments for this pass
                state_doc = load_state(settings.state_file)
                pending_segments_list = [
                    seg
                    for seg in segments_doc.segments
                    if not state_doc.segments.get(seg.segment_id)
                    or state_doc.segments.get(seg.segment_id).status != SegmentStatus.COMPLETED
                ]

                if not pending_segments_list:
                    break

                pass_successes = 0
                pass_failures: list[str] = []

                # Use parallel translation with ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all pending segments
                    future_to_segment = {}
                    for segment in pending_segments_list:
                        future = executor.submit(
                            _translate_segment,
                            segment,
                            provider,
                            source_language,
                            target_language,
                        )
                        future_to_segment[future] = segment
                        active_workers += 1

                    # Process results as they complete
                    try:
                        for future in as_completed(future_to_segment):
                            active_workers -= 1
                            segment = future_to_segment[future]
                            result = future.result()

                            # Update state based on result
                            if result.is_auto_copy:
                                mark_status(
                                    settings.state_file,
                                    result.segment_id,
                                    SegmentStatus.COMPLETED,
                                    translation=result.translation,
                                    provider_name=None,
                                    model_name=None,
                                    error_message=None,
                                )
                                file_completed[segment.file_path] += 1
                                completed_segments += 1
                                pending_segments -= 1
                                text = _truncate_text(_strip_tags(result.translation))
                                preview_lines[preview_index] = f"[dim]{text}[/dim]"
                                pass_successes += 1
                            elif result.error:
                                logger.error("Translation failed for %s: %s", result.segment_id, result.error)
                                mark_status(
                                    settings.state_file,
                                    result.segment_id,
                                    SegmentStatus.ERROR,
                                    error_message=str(result.error),
                                )
                                error_msg = _truncate_text(str(result.error))
                                preview_lines[preview_index] = f"[red]{error_msg}[/red]"
                                pass_failures.append(result.segment_id)

                                # Track consecutive failures and trigger cooldown if needed
                                trans_state = load_state(settings.state_file)
                                consecutive = trans_state.consecutive_failures + 1
                                set_consecutive_failures(settings.state_file, consecutive)

                                if consecutive >= 3:
                                    in_cooldown = True
                                    cooldown_until = datetime.utcnow() + timedelta(minutes=30)
                                    set_cooldown(settings.state_file, cooldown_until)
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

                                    set_cooldown(settings.state_file, None)
                                    set_consecutive_failures(settings.state_file, 0)
                                    in_cooldown = False
                                    cooldown_remaining = ""
                            else:
                                mark_status(
                                    settings.state_file,
                                    result.segment_id,
                                    SegmentStatus.COMPLETED,
                                    translation=result.translation,
                                    provider_name=result.provider_name,
                                    model_name=result.model_name,
                                    error_message=None,
                                )
                                file_completed[segment.file_path] += 1
                                completed_segments += 1
                                pending_segments -= 1
                                text = _truncate_text(_strip_tags(result.translation))
                                preview_lines[preview_index] = f"[green]{text}[/green]"
                                pass_successes += 1
                                # Reset consecutive failures on any success
                                set_consecutive_failures(settings.state_file, 0)

                            # Round-robin through preview slots
                            preview_index = (preview_index + 1) % max_workers

                            progress.advance(task_id)
                            live.update(Group(render_panel(), progress))

                    except KeyboardInterrupt:
                        console.print("\n[yellow]Interrupted by user. Canceling pending translations...[/yellow]")
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise

                # After each pass, check if we should retry failed segments
                if pass_failures:
                    if pass_successes == 0:
                        # No progress made, stop trying
                        break
                    # Reset preview lines to "waiting…" for next pass
                    preview_lines = ["waiting…"] * max_workers
                    preview_index = 0
                    reset_error_segments(settings.state_file, pass_failures)
                    continue

        except KeyboardInterrupt:
            raise

    # Note: Polish is now applied incrementally after each translation (see line 203)
    # No separate polish pass needed at the end
