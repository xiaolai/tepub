from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import AppSettings
from config.workspace import assert_same_book
from console_singleton import get_console
from glossary import glossary_for
from logging_utils.logger import get_logger
from state.models import ExtractMode, SegmentStatus
from state.store import backup_state, ensure_state, load_segments, load_state
from state.writer import StateWriter, exclusive_run
from translation.languages import describe_language, normalize_language
from translation.markup import markup_mismatch, protect, restore
from translation.polish import polish_translation
from translation.providers import (
    ProviderError,
    ProviderFatalError,
    ReplyRejectedError,
    create_provider,
)
from translation.refusal_filter import looks_like_refusal

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

# Consecutive failures that start a cooldown, how long it lasts, and how many one
# run may take before it stops and says so.
FAILURES_BEFORE_COOLDOWN = 3
COOLDOWN = timedelta(minutes=30)
MAX_COOLDOWNS_PER_RUN = 3

# Indirection so tests can run a cooldown without waiting thirty minutes.
_sleep = time.sleep


class TranslationResult:
    """Result of translating a single segment."""

    __slots__ = (
        "segment_id",
        "translation",
        "error",
        "is_auto_copy",
        "provider_name",
        "model_name",
    )

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


def _reply(segment, provider, source_language: str, target_language: str) -> str:
    """One provider call, refused if it declines the task, polished if not."""
    text = provider.translate(
        segment, source_language=source_language, target_language=target_language
    )
    # A refusal is not a translation. This check used to run only in the debug
    # purge command, so refusals were stored as COMPLETED and exported. A source
    # that itself reads like a refusal is translated as normal.
    if looks_like_refusal(text) and not looks_like_refusal(segment.source_content):
        preview = " ".join(text.split())[:80]
        raise ReplyRejectedError(f"Provider refused segment {segment.segment_id}: {preview!r}")
    return polish_translation(text)


def _language_codes(source_language: str, target_language: str) -> tuple[str, str]:
    return normalize_language(source_language)[0], normalize_language(target_language)[0]


def select_for_translation(segments: list, settings: AppSettings) -> list:
    """The units a translate run works on: those in the book config's
    translation_files when it has the list, otherwise those not skipped."""
    if settings.translation_files is not None:
        allowed_files = set(settings.translation_files)
        return [seg for seg in segments if seg.file_path.as_posix() in allowed_files]
    return [seg for seg in segments if seg.skip_reason is None]


def _translate_checked(
    segment, provider, source_language: str, target_language: str, glossary=None
) -> str:
    """Translate, holding the reply to the markup contract (D6) and the glossary.

    An HTML unit with only inline markup goes to a language model as text with
    numbered markers, and its tags are rebuilt from the source afterwards (see
    translation.markup). The glossary's renderings for the unit's terms go in
    the prompt. A reply that changed the markup or missed a rendering is
    retried once with the problems named; a second reply that changes the
    markup is an error, while a missed rendering is only logged, since a
    translator may rightly avoid repeating a term. `tepub glossary check`
    lists those. The checks run after polishing, which rewrites the string.
    """
    html_unit = segment.extract_mode == ExtractMode.HTML
    protected = protect(segment.source_content) if html_unit and provider.uses_markers else None
    sent = segment
    keep = "Keep every tag and every href, src and id value exactly as in the source"
    if protected is not None:
        sent = segment.model_copy(
            update={"source_content": protected[0], "extract_mode": ExtractMode.TEXT}
        )
        keep = "Keep every numbered marker exactly once, unchanged, and every line break"

    def rebuild(text: str) -> str:
        return restore(text, protected[1]) if protected is not None else text

    terms = glossary.terms_in(segment.source_content) if glossary is not None else []
    if terms:
        hints = {term.source: term.target for term in terms}
        sent = sent.model_copy(update={"metadata": sent.metadata.model_copy(update={"terms": hints})})

    def problems(text: str) -> tuple[str | None, list[str]]:
        markup = markup_mismatch(segment.source_content, text) if html_unit else None
        return markup, glossary.problems(terms, text) if terms else []

    text = rebuild(_reply(sent, provider, source_language, target_language))
    markup, missed = problems(text)
    if markup is None and (not missed or not provider.follows_instructions):
        _log_missed(segment, missed)
        return text
    notes = []
    if markup is not None:
        notes.append(f"Your previous translation changed the markup ({markup}). {keep}; translate only the text.")
    if missed:
        notes.append(f"Your previous translation did not follow the glossary: {'; '.join(missed)}.")
    retry = sent.model_copy(update={"metadata": sent.metadata.model_copy(update={"notes": " ".join(notes)})})
    first, first_markup, first_missed = text, markup, missed
    text = rebuild(_reply(retry, provider, source_language, target_language))
    markup, missed = problems(text)
    if markup is not None:
        if first_markup is None:
            # Retried for the glossary alone, and the retry broke the markup:
            # the first reply is a usable translation that missed a term.
            _log_missed(segment, first_missed)
            return first
        raise ReplyRejectedError(
            f"Translation of segment {segment.segment_id} changed the markup twice: {markup}"
        )
    _log_missed(segment, missed)
    return text


def _log_missed(segment, missed: list[str]) -> None:
    if missed:
        logger.warning("Glossary not followed in %s: %s", segment.segment_id, "; ".join(missed))


def _translate_segment(
    segment,
    provider,
    source_language: str,
    target_language: str,
    glossary=None,
) -> TranslationResult:
    """Translate a single segment. Thread-safe worker function.

    Args:
        segment: The segment to translate
        provider: Translation provider instance
        source_language: Source language code
        target_language: Target language code
        glossary: The book's glossary, if it has one

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
        # Enforce the provider's declared capabilities before spending a call.
        ensure_supported = getattr(provider, "ensure_segment_supported", None)
        if ensure_supported is not None:
            ensure_supported(segment)

        polished_text = _translate_checked(
            segment, provider, source_language, target_language, glossary
        )
        return TranslationResult(
            segment_id=segment.segment_id,
            translation=polished_text,
            provider_name=provider.name,
            model_name=provider.model,
        )
    except ProviderFatalError as exc:
        # Kept distinct from ProviderError so the run loop can abort. Folding the
        # two together meant a fatal condition — a rejected API key, say — was
        # retried once per segment against every remaining segment.
        return TranslationResult(
            segment_id=segment.segment_id,
            error=exc,
        )
    except ProviderError as exc:
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
    # Segments from another book describe other documents, so translating them
    # corrupts the output. The check is shared with export (config.workspace).
    assert_same_book(segments_doc, input_epub)

    # Filter segments based on translation_files inclusion list or skip metadata
    original_count = len(segments_doc.segments)
    segments_doc.segments = select_for_translation(segments_doc.segments, settings)

    filtered_count = original_count - len(segments_doc.segments)
    if filtered_count > 0:
        filter_type = "inclusion list" if settings.translation_files is not None else "skip rules"
        console.print(
            f"[cyan]Filtered {filtered_count} segments from {original_count} "
            f"based on {filter_type}[/cyan]"
        )

    # Early exit if all segments were filtered out
    if len(segments_doc.segments) == 0:
        if settings.translation_files is not None:
            console.print(
                "[yellow]No segments to translate. All segments were filtered out by the "
                "translation_files inclusion list in config.yaml.[/yellow]"
            )
            console.print(
                "[yellow]This may indicate a mismatch between config.yaml and segments.json. "
                "Try deleting the work directory and re-running extraction.[/yellow]"
            )
        else:
            console.print("[yellow]No segments to translate. All segments were skipped.[/yellow]")
        return

    # One run per workspace: a second process used to translate every segment
    # again, and the last to save won.
    with exclusive_run(settings.state_file):
        provider = create_provider(settings.primary_provider)
        provider.preflight()
        glossary = glossary_for(settings.work_root, settings.work_dir, target_language)
        if glossary is not None:
            console.print(f"[cyan]Glossary: {len(glossary.decided())} terms[/cyan]")

        # Translations into another language do not carry over. Keep a copy and say
        # how much is being reset; this used to happen silently inside ensure_state,
        # which left the message that should have announced it unreachable.
        if settings.state_file.exists():
            previous = load_state(settings.state_file)
            # Compared as language codes: "Simplified Chinese" and "zh-CN" are one
            # language, and comparing spellings reset a translated book.
            if _language_codes(previous.source_language, previous.target_language) != (
                _language_codes(source_language, target_language)
            ):
                finished = sum(
                    1 for r in previous.segments.values() if r.status == SegmentStatus.COMPLETED
                )
                backup = backup_state(settings.state_file)
                noun = "translation" if finished == 1 else "translations"
                console.print(
                    f"[yellow]The languages changed: this workspace translated "
                    f"{previous.source_language} into {previous.target_language}, and this run "
                    f"translates {source_language} into {target_language}. Resetting "
                    f"{finished} finished {noun}; the previous state is saved as "
                    f"{backup.name}.[/yellow]"
                )
                settings.state_file.unlink()

        state_doc = ensure_state(
            settings.state_file,
            segments_doc.segments,
            provider.name,
            provider.model,
            source_language,
            target_language,
        )

        console.print(
            f"Translating from {describe_language(source_language)} into "
            f"{describe_language(target_language)} using {provider.model}"
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

        cooldowns_taken = 0
        dashboard = Live(
            Group(render_panel(), progress), console=console, refresh_per_second=5
        )
        with StateWriter(settings.state_file) as writer, dashboard as live:
            # Failures in a row are counted within a run. The count is saved with
            # the state, and one left at 3 by an earlier run started a 30-minute
            # cooldown on this run's first failure.
            writer.consecutive_failures = 0
            try:
                while True:
                    # Reload state to get pending segments for this pass
                    state_doc = writer.doc
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
                    fatal_error: Exception | None = None
                    cooled_down_this_pass = False

                    # Use parallel translation with ThreadPoolExecutor
                    # Not a `with` block: its __exit__ calls shutdown(wait=True), which
                    # negated the wait=False fast path on KeyboardInterrupt below.
                    executor = ThreadPoolExecutor(max_workers=max_workers)
                    interrupted = False
                    try:
                        # Submit all pending segments
                        future_to_segment = {}
                        for segment in pending_segments_list:
                            future = executor.submit(
                                _translate_segment,
                                segment,
                                provider,
                                source_language,
                                target_language,
                                glossary,
                            )
                            future_to_segment[future] = segment
                            active_workers += 1

                        # Process results as they complete
                        try:
                            for future in as_completed(future_to_segment):
                                active_workers -= 1
                                segment = future_to_segment[future]
                                if future.cancelled():
                                    # Cancelled when cooldown began; stays pending.
                                    continue
                                result = future.result()

                                # Update state based on result
                                if result.is_auto_copy:
                                    writer.mark(
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
                                    logger.error(
                                        "Translation failed for %s: %s",
                                        result.segment_id,
                                        result.error,
                                    )
                                    writer.mark(
                                        result.segment_id,
                                        SegmentStatus.ERROR,
                                        error_message=str(result.error),
                                    )
                                    if isinstance(result.error, ProviderFatalError):
                                        # Fatal means the whole run cannot succeed — a
                                        # rejected key or unusable model. Stop scheduling
                                        # instead of repeating it against every remaining
                                        # segment. The run still returns normally so
                                        # completed work stays saved and resumable.
                                        fatal_error = result.error
                                        for queued in future_to_segment:
                                            if not queued.done():
                                                queued.cancel()
                                        console.print(
                                            f"[red]Fatal provider error: {result.error}[/red]"
                                        )
                                        console.print(
                                            "[yellow]Stopping run; completed translations are "
                                            "saved and the run can be resumed.[/yellow]"
                                        )
                                        break
                                    error_msg = _truncate_text(str(result.error))
                                    preview_lines[preview_index] = f"[red]{error_msg}[/red]"
                                    pass_failures.append(result.segment_id)

                                    # Track consecutive failures and trigger cooldown if
                                    # needed. A reply rejected for its content is not a
                                    # sign of an unwell provider: three in a row once
                                    # stopped a local model for 30 minutes to no purpose.
                                    counted = not isinstance(result.error, ReplyRejectedError)
                                    consecutive = writer.consecutive_failures + int(counted)
                                    writer.consecutive_failures = consecutive

                                    if counted and consecutive >= FAILURES_BEFORE_COOLDOWN:
                                        in_cooldown = True
                                        cooled_down_this_pass = True
                                        cooldowns_taken += 1
                                        # Every pending segment was already submitted, so
                                        # without cancelling, workers kept calling the
                                        # provider throughout the cooldown — exactly what
                                        # the cooldown exists to prevent. Cancelled
                                        # segments stay pending for the next pass.
                                        for queued in future_to_segment:
                                            if not queued.done():
                                                queued.cancel()
                                        cooldown_until = datetime.now(timezone.utc) + COOLDOWN
                                        writer.set_cooldown(cooldown_until)
                                        # Count down by the time actually slept rather than
                                        # re-reading the clock, so the wait is bounded.
                                        remaining = COOLDOWN.total_seconds()
                                        while remaining > 0:
                                            mins = int(remaining // 60)
                                            secs = int(remaining % 60)
                                            cooldown_remaining = f"{mins}m {secs}s"
                                            live.update(Group(render_panel(), progress))
                                            sleep_for = min(5, remaining)
                                            _sleep(sleep_for)
                                            remaining -= sleep_for

                                        writer.set_cooldown(None)
                                        writer.consecutive_failures = 0
                                        in_cooldown = False
                                        cooldown_remaining = ""
                                else:
                                    writer.mark(
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
                                    writer.consecutive_failures = 0

                                # Round-robin through preview slots
                                preview_index = (preview_index + 1) % max_workers

                                progress.advance(task_id)
                                live.update(Group(render_panel(), progress))

                        except KeyboardInterrupt:
                            interrupted = True
                            console.print(
                                "\n[yellow]Interrupted by user. "
                                "Canceling pending translations...[/yellow]"
                            )
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise
                    finally:
                        if not interrupted:
                            executor.shutdown(wait=True)

                    if fatal_error is not None:
                        # Retrying cannot help: the provider itself is unusable.
                        break

                    # A cooldown cancelled the rest of this pass; the provider has had
                    # its rest, so start the next pass instead of judging this one. It
                    # used to count as "no progress" and end the run after the wait.
                    if cooled_down_this_pass:
                        if cooldowns_taken >= MAX_COOLDOWNS_PER_RUN:
                            console.print(
                                f"[red]The provider kept failing after {cooldowns_taken} "
                                "cooldowns; stopping. Completed translations are saved; "
                                "run the command again to continue.[/red]"
                            )
                            break
                        preview_lines = ["waiting…"] * max_workers
                        preview_index = 0
                        writer.reset_errors(pass_failures)
                        continue

                    # After each pass, check if we should retry failed segments
                    if pass_failures:
                        if pass_successes == 0:
                            # No progress made, stop trying
                            break
                        # Reset preview lines to "waiting…" for next pass
                        preview_lines = ["waiting…"] * max_workers
                        preview_index = 0
                        writer.reset_errors(pass_failures)
                        continue

            except KeyboardInterrupt:
                raise

    # Note: Polish is now applied incrementally after each translation (see line 203)
    # No separate polish pass needed at the end
