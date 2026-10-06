from __future__ import annotations

import shlex
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import AppSettings
from config.workspace import assert_same_book
from console_singleton import PlainProgress, get_console
from console_singleton import live as live_display
from glossary import glossary_for
from logging_utils.logger import get_logger
from state.models import ExtractMode, SegmentStatus, TranslationRecord
from state.store import backup_state, ensure_state, load_segments, load_state, save_state
from state.writer import StateWriter, exclusive_run
from translation.languages import describe_language, normalize_language
from translation.markup import markup_mismatch, protect, restore, stray_tags, strip_code_fence
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
        label = f"w{i + 1}" if max_workers > 1 else "current"
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

# Calls submitted ahead of the results read, per worker: enough to keep every
# worker busy, few enough that a stop or a cooldown takes effect within a
# bounded number of calls. Submitting the whole book at once let a single
# worker call the provider thousands of times before a fatal error was read.
SUBMITTED_PER_WORKER = 2

# Indirection so tests can run a cooldown without waiting thirty minutes.
_sleep = time.sleep


class UnexpectedError(ProviderError):
    """A unit failed in tepub's own code rather than at the provider; the
    traceback is logged where it was raised."""


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
    text = strip_code_fence(
        provider.translate(
            segment, source_language=source_language, target_language=target_language
        )
    )
    # A refusal is not a translation. This check used to run only in the debug
    # purge command, so refusals were stored as COMPLETED and exported. A source
    # that itself reads like a refusal is translated as normal.
    if looks_like_refusal(text) and not looks_like_refusal(segment.source_content):
        preview = " ".join(text.split())[:80]
        raise ReplyRejectedError(f"Provider refused segment {segment.segment_id}: {preview!r}")
    polished = polish_translation(text)
    # The provider's own emptiness check sees the reply with its fence, so a
    # fenced empty reply passed it and was stored as a finished translation.
    if not polished.strip():
        raise ReplyRejectedError(f"Provider returned an empty translation of {segment.segment_id}")
    return polished


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
    if html_unit:
        keep = "Keep every tag and every href, src and id value exactly as in the source"
    else:
        keep = "Return plain text, with no HTML tags and no code fences"
    if protected is not None:
        sent = segment.model_copy(
            update={"source_content": protected[0], "extract_mode": ExtractMode.TEXT}
        )
        keep = (
            "Keep every numbered marker exactly once, unchanged, and every line break, "
            "and add no HTML tags"
        )

    def rebuild(text: str) -> str:
        return restore(text, protected[1]) if protected is not None else text

    terms = glossary.terms_in(segment.source_content) if glossary is not None else []
    if terms:
        hints = {term.source: term.target for term in terms}
        metadata = sent.metadata.model_copy(update={"terms": hints})
        sent = sent.model_copy(update={"metadata": metadata})

    def problems(raw: str, text: str) -> tuple[str | None, list[str]]:
        if sent.extract_mode == ExtractMode.HTML:
            markup = markup_mismatch(segment.source_content, text)
        else:
            # Plain text, or text with markers: any tag in the reply is the
            # model's, and would reach the page as literal text.
            markup = stray_tags(sent.source_content, raw)
            if markup is None and html_unit:
                markup = markup_mismatch(segment.source_content, text)
        return markup, glossary.problems(terms, text) if terms else []

    raw = _reply(sent, provider, source_language, target_language)
    text = rebuild(raw)
    markup, missed = problems(raw, text)
    if markup is None and (not missed or not provider.follows_instructions):
        _log_missed(segment, missed)
        return text
    notes = []
    if markup is not None:
        notes.append(
            f"Your previous translation changed the markup ({markup}). "
            f"{keep}; translate only the text."
        )
    if missed:
        notes.append(f"Your previous translation did not follow the glossary: {'; '.join(missed)}.")
    metadata = sent.metadata.model_copy(update={"notes": " ".join(notes)})
    retry = sent.model_copy(update={"metadata": metadata})
    first, first_markup, first_missed = text, markup, missed
    raw = _reply(retry, provider, source_language, target_language)
    text = rebuild(raw)
    markup, missed = problems(raw, text)
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
    except ProviderError as exc:
        # ProviderFatalError included: the run loop tells it apart to abort the
        # run, rather than retry a rejected API key against every segment.
        return TranslationResult(segment_id=segment.segment_id, error=exc)
    except Exception as exc:
        # A failure in tepub's own code, not at the provider. It was reduced to
        # its message, so the traceback was lost, and it counted toward a
        # cooldown as if the provider were unwell.
        logger.exception("Unexpected error translating %s", segment.segment_id)
        error = UnexpectedError(f"Unexpected error: {type(exc).__name__}: {exc}")
        error.__cause__ = exc
        return TranslationResult(segment_id=segment.segment_id, error=error)


def _counts_toward_cooldown(error: Exception) -> bool:
    # A reply rejected for its content, or a failure in tepub itself, is no sign
    # of an unwell provider: three rejected replies in a row once stopped a local
    # model for 30 minutes to no purpose.
    return not isinstance(error, (ReplyRejectedError, UnexpectedError))


def _is_pending(segment, records) -> bool:
    record = records.get(segment.segment_id)
    return record is None or record.status != SegmentStatus.COMPLETED


def _cancel_queued(futures) -> None:
    """Cancel the calls not yet started; those already running finish."""
    for future in futures:
        if not future.done():
            future.cancel()


def _announce_stop(reason: str) -> None:
    console.print(f"[red]{escape(reason)}[/red]")
    console.print(
        "[yellow]Stopping run; completed translations are saved and the run can be "
        "resumed.[/yellow]"
    )


def _wait_out_cooldown(writer: StateWriter, show) -> None:
    """Sleep through one cooldown, passing the time left to ``show``."""
    writer.set_cooldown(datetime.now(timezone.utc) + COOLDOWN)
    # Count down by the time actually slept rather than re-reading the clock, so
    # the wait is bounded.
    remaining = COOLDOWN.total_seconds()
    while remaining > 0:
        show(f"{int(remaining // 60)}m {int(remaining % 60)}s")
        sleep_for = min(5, remaining)
        _sleep(sleep_for)
        remaining -= sleep_for
    writer.set_cooldown(None)
    writer.consecutive_failures = 0
    show("")


def _check_local_provider(provider, writer: StateWriter) -> str | None:
    """A local server: ask it rather than wait. If it answers, carry on at once;
    if not, return why the run must stop, which says how to start it."""
    try:
        provider.preflight()
    except ProviderFatalError as exc:
        return str(exc)
    writer.consecutive_failures = 0
    return None


def _run_translation(
    settings: AppSettings,
    input_epub: Path,
    *,
    source_language: str,
    target_language: str,
) -> int:
    """Translate the selected units still to do; returns how many this run
    completed. The caller holds the workspace lock."""
    settings.ensure_directories()

    segments_doc = load_segments(settings.segments_file)
    # Segments from another book describe other documents, so translating them
    # corrupts the output. The check is shared with export (config.workspace).
    assert_same_book(segments_doc, input_epub)

    # Translations into another language do not carry over. Keep a copy and say
    # how much is being reset; this used to happen silently inside ensure_state,
    # which left the message that should have announced it unreachable. Settled
    # before selection, which can leave nothing to translate.
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
                escape(
                    f"The languages changed: this workspace translated "
                    f"{previous.source_language} into {previous.target_language}, and this "
                    f"run translates {source_language} into {target_language}. Resetting "
                    f"{finished} finished {noun}; the previous state is saved as "
                    f"{backup.name}."
                ),
                style="yellow",
            )
            # Reset in place, recording the run's languages even when it has
            # nothing to translate (export names its files after them), and
            # keeping each record's source digest from extraction.
            previous.segments = {
                sid: TranslationRecord(segment_id=sid, source_sha256=record.source_sha256)
                for sid, record in previous.segments.items()
            }
            previous.source_language = source_language
            previous.target_language = target_language
            save_state(previous, settings.state_file)

    # Filter segments based on translation_files inclusion list or skip metadata
    original_count = len(segments_doc.segments)
    segments_doc.segments = select_for_translation(segments_doc.segments, settings)
    selected = segments_doc.segments

    filtered_count = original_count - len(selected)
    if filtered_count > 0:
        filter_type = "inclusion list" if settings.translation_files is not None else "skip rules"
        console.print(
            f"[cyan]Filtered {filtered_count} segments from {original_count} "
            f"based on {filter_type}[/cyan]"
        )

    # Early exit if all segments were filtered out
    if not selected:
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
        return 0

    glossary = glossary_for(settings.work_root, settings.work_dir, target_language)
    if glossary is not None:
        console.print(f"[cyan]Glossary: {len(glossary.decided())} terms[/cyan]")

    records = load_state(settings.state_file).segments if settings.state_file.exists() else {}
    to_do = [seg for seg in selected if _is_pending(seg, records)]
    if not to_do:
        console.print("[green]All segments already translated.[/green]")
        return 0

    # The provider is created and checked only when a unit needs it: a finished
    # book, or one with only units copied as they are left, used to fail when
    # the provider was unreachable or its key missing.
    provider = None
    if any(not should_auto_copy(seg) for seg in to_do):
        provider = create_provider(settings.primary_provider)
        provider.preflight()
    provider_name = provider.name if provider is not None else settings.primary_provider.name
    model_name = provider.model if provider is not None else settings.primary_provider.model

    state_doc = ensure_state(
        settings.state_file,
        selected,
        provider_name,
        model_name,
        source_language,
        target_language,
    )

    console.print(
        escape(
            f"Translating from {describe_language(source_language)} into "
            f"{describe_language(target_language)} using {model_name}"
        )
    )

    view = _Dashboard(
        selected,
        state_doc.segments,
        skipped_files=len({doc.file_path for doc in segments_doc.skipped_documents}),
        max_workers=settings.translation_workers,
    )
    translated = 0
    cooldowns_taken = 0
    with StateWriter(settings.state_file) as writer, view:
        # Failures in a row are counted within a run. The count is saved with
        # the state, and one left at 3 by an earlier run started a 30-minute
        # cooldown on this run's first failure.
        writer.consecutive_failures = 0
        # Units that failed in tepub's own code; not retried in this run.
        broken: set[str] = set()
        while True:
            pending_list = [
                seg
                for seg in selected
                if seg.segment_id not in broken and _is_pending(seg, writer.doc.segments)
            ]
            if not pending_list:
                break

            outcome = _run_pass(
                pending_list,
                provider=provider,
                source_language=source_language,
                target_language=target_language,
                glossary=glossary,
                writer=writer,
                view=view,
                cooldowns_taken=cooldowns_taken,
            )
            translated += outcome.successes
            cooldowns_taken += outcome.cooled_down
            broken.update(outcome.broken)

            if outcome.stop_reason is not None:
                # Retrying cannot help: the provider itself is unusable.
                break

            # A cooldown cancelled the rest of this pass; the provider has had its
            # rest, so start the next pass instead of judging this one. It used
            # to count as "no progress" and end the run after the wait.
            if outcome.cooled_down or outcome.failures:
                if not outcome.cooled_down and outcome.successes == 0:
                    # No progress made, stop trying
                    break
                view.new_pass()
                writer.reset_errors(list(outcome.failures))
                continue

    return translated


class _Dashboard:
    """What a run shows as it goes: the panel and progress bar on a terminal,
    occasional plain lines elsewhere. Used as a context manager around the run."""

    def __init__(self, selected: list, records, *, skipped_files: int, max_workers: int):
        self.total = len(selected)
        # Counted over the selected units only: the state also holds excluded
        # files and units a re-extraction dropped, which made the counts exceed
        # the total.
        self.file_totals: Counter[Path] = Counter(seg.file_path for seg in selected)
        self.file_completed: Counter[Path] = Counter(
            seg.file_path for seg in selected if not _is_pending(seg, records)
        )
        self.completed = sum(self.file_completed.values())
        self.skipped_files = skipped_files
        self.max_workers = max_workers
        # The pass's submitted calls, queued and running; set by _run_pass.
        self.in_flight: dict[Future, object] = {}
        # Track recent completions (one per worker slot) for display
        self.preview_lines = ["waiting…"] * max_workers
        self.preview_index = 0  # Round-robin index for updating preview lines
        self.cooldown_remaining = ""
        self.progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            auto_refresh=False,
        )
        self.task_id = self.progress.add_task(
            "Translating", total=self.total, completed=self.completed
        )
        self._live = live_display(self._renderable(), console=console, refresh_per_second=5)
        self._shown = self._live
        self.plain = PlainProgress("Translated", self.total, console=console)

    def __enter__(self) -> _Dashboard:
        self._shown = self._live.__enter__()
        return self

    def __exit__(self, *exc: object) -> bool:
        return self._live.__exit__(*exc)

    def _render_panel(self) -> Panel:
        return _build_dashboard_panel(
            total_files=len(self.file_totals),
            skipped_files=self.skipped_files,
            completed_files=sum(
                1
                for path, total_required in self.file_totals.items()
                if self.file_completed[path] >= total_required
            ),
            total_segments=self.total,
            completed_segments=self.completed,
            pending_segments=self.total - self.completed,
            preview_lines=self.preview_lines,
            # Running calls only: a submitted call may still be queued, or
            # finished or cancelled and not yet collected.
            active_workers=sum(1 for future in tuple(self.in_flight) if future.running()),
            max_workers=self.max_workers,
            in_cooldown=bool(self.cooldown_remaining),
            cooldown_remaining=self.cooldown_remaining,
        )

    def _renderable(self) -> Group:
        return Group(self._render_panel(), self.progress)

    def refresh(self) -> None:
        self._shown.update(self._renderable())

    def show_cooldown(self, remaining: str) -> None:
        self.cooldown_remaining = remaining
        self.refresh()

    def succeeded(self, segment, result: TranslationResult) -> None:
        self.file_completed[segment.file_path] += 1
        self.completed += 1
        # Progress counts successes only: counting attempts reported a book
        # finished while units were still failing.
        self.progress.advance(self.task_id)
        self.plain.report(self.completed)
        style = "dim" if result.is_auto_copy else "green"
        text = escape(_truncate_text(_strip_tags(result.translation)))
        self.preview_lines[self.preview_index] = f"[{style}]{text}[/{style}]"

    def failed(self, error: Exception) -> None:
        error_msg = escape(_truncate_text(str(error)))
        self.preview_lines[self.preview_index] = f"[red]{error_msg}[/red]"

    def next_slot(self) -> None:
        # Round-robin through preview slots
        self.preview_index = (self.preview_index + 1) % self.max_workers
        self.refresh()

    def new_pass(self) -> None:
        self.preview_lines = ["waiting…"] * self.max_workers
        self.preview_index = 0


@dataclass(frozen=True)
class _PassOutcome:
    """What one pass over the pending units did."""

    successes: int
    # Failures worth another attempt in this run.
    failures: tuple[str, ...]
    # Units that failed in tepub's own code: another attempt would hit the same
    # bug, so they stay failed until the next run.
    broken: tuple[str, ...]
    # A cooldown was taken; it cancels the rest of the pass, so at most one.
    cooled_down: bool
    stop_reason: str | None


def _run_pass(
    pending: list,
    *,
    provider,
    source_language: str,
    target_language: str,
    glossary,
    writer: StateWriter,
    view: _Dashboard,
    cooldowns_taken: int,
) -> _PassOutcome:
    """Submit the pending units a few at a time, record each result as it
    arrives, and stop or cool down when the failures call for it."""
    successes = 0
    failures: list[str] = []
    broken: list[str] = []
    cooled_down = False
    stop_reason: str | None = None

    # Not a `with` block: its __exit__ calls shutdown(wait=True), which
    # negated the wait=False fast path on KeyboardInterrupt below.
    executor = ThreadPoolExecutor(max_workers=view.max_workers)
    interrupted = False
    to_submit = iter(pending)
    in_flight: dict[Future, object] = {}
    view.in_flight = in_flight
    try:
        while True:
            # Nothing more is submitted once the pass stops or cools down; the
            # units not submitted stay pending.
            while (
                stop_reason is None
                and not cooled_down
                and len(in_flight) < SUBMITTED_PER_WORKER * view.max_workers
            ):
                segment = next(to_submit, None)
                if segment is None:
                    break
                future = executor.submit(
                    _translate_segment,
                    segment,
                    provider,
                    source_language,
                    target_language,
                    glossary,
                )
                in_flight[future] = segment
            if not in_flight:
                break

            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                segment = in_flight.pop(future)
                if future.cancelled():
                    # Cancelled by a cooldown or a stop; stays pending.
                    continue
                result = future.result()
                # Calls already running when the run stopped or cooled down
                # still finish. A success is kept: it used to be discarded, paid
                # for and lost. A provider failure says nothing new about the
                # provider, so the unit is left pending for the next run.
                settled = stop_reason is not None or cooled_down
                if result.error is None:
                    successes += 1
                elif isinstance(result.error, UnexpectedError):
                    # A bug in tepub: retrying it, in this pass or the next,
                    # would only repeat it. Recorded even when settled, since
                    # it says nothing about the provider either way.
                    broken.append(result.segment_id)
                elif not settled:
                    failures.append(result.segment_id)
                stop_reason, cooled_down = _record_result(
                    segment,
                    result,
                    settled=settled,
                    provider=provider,
                    writer=writer,
                    view=view,
                    in_flight=in_flight,
                    cooldowns_taken=cooldowns_taken,
                    stop_reason=stop_reason,
                    cooled_down=cooled_down,
                )
                view.next_slot()

    except KeyboardInterrupt:
        # Anywhere in the pass, submitting included: an interrupt outside this
        # handler left the finally below waiting for every worker, a call that
        # may run for minutes.
        interrupted = True
        console.print("\n[yellow]Interrupted by user. Canceling pending translations...[/yellow]")
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if not interrupted:
            executor.shutdown(wait=True)

    return _PassOutcome(
        successes=successes,
        failures=tuple(failures),
        broken=tuple(broken),
        cooled_down=cooled_down,
        stop_reason=stop_reason,
    )


def _record_result(
    segment,
    result: TranslationResult,
    *,
    settled: bool,
    provider,
    writer: StateWriter,
    view: _Dashboard,
    in_flight: dict[Future, object],
    cooldowns_taken: int,
    stop_reason: str | None,
    cooled_down: bool,
) -> tuple[str | None, bool]:
    """Save one result and judge what it says about the provider; returns the
    pass's stop reason and whether it has cooled down."""
    if result.error is None:
        writer.mark(
            result.segment_id,
            SegmentStatus.COMPLETED,
            translation=result.translation,
            provider_name=result.provider_name,
            model_name=result.model_name,
            error_message=None,
        )
        view.succeeded(segment, result)
        # A unit copied as it is never reached the provider, so it says
        # nothing about the provider's health.
        if not result.is_auto_copy:
            writer.consecutive_failures = 0
        return stop_reason, cooled_down

    if settled and not isinstance(result.error, UnexpectedError):
        logger.error(
            "Translation failed for %s after the run stopped or cooled down; left pending: %s",
            result.segment_id,
            result.error,
        )
        return stop_reason, cooled_down

    logger.error("Translation failed for %s: %s", result.segment_id, result.error)
    writer.mark(result.segment_id, SegmentStatus.ERROR, error_message=str(result.error))
    view.failed(result.error)
    if settled:
        return stop_reason, cooled_down

    if isinstance(result.error, ProviderFatalError):
        # Fatal means the whole run cannot succeed — a rejected key or unusable
        # model. Stop scheduling instead of repeating it against every remaining
        # segment. The run still returns normally so completed work stays saved
        # and resumable.
        stop_reason = f"Fatal provider error: {result.error}"
    elif _counts_toward_cooldown(result.error):
        writer.consecutive_failures += 1
        if writer.consecutive_failures >= FAILURES_BEFORE_COOLDOWN:
            if getattr(provider, "local", False):
                stop_reason = _check_local_provider(provider, writer)
            elif cooldowns_taken >= MAX_COOLDOWNS_PER_RUN:
                # Checked before waiting: the last allowed cooldown used to be
                # followed by a stop, so its thirty minutes bought nothing.
                stop_reason = f"The provider kept failing after {cooldowns_taken} cooldowns."
            else:
                # Without cancelling, the queued calls would reach the provider
                # during the cooldown — what the cooldown exists to prevent.
                # Cancelled segments stay pending for the next pass.
                _cancel_queued(in_flight)
                cooled_down = True
                _wait_out_cooldown(writer, view.show_cooldown)

    if stop_reason is not None:
        _cancel_queued(in_flight)
        _announce_stop(stop_reason)
    return stop_reason, cooled_down


@dataclass(frozen=True)
class RunSummary:
    """Where a book stands after a translate run."""

    total: int
    completed: int
    failed: tuple[str, ...]
    pending: int
    translated_now: int
    seconds: float
    glossary_misses: int

    @property
    def ok(self) -> bool:
        # Finished, not merely free of failures: a run stopped early leaves
        # units pending and no failure behind.
        return not self.failed and not self.pending


def _summarise(
    settings: AppSettings, translated_now: int, started: float, target_language: str
) -> RunSummary:
    segments = select_for_translation(load_segments(settings.segments_file).segments, settings)
    state = load_state(settings.state_file) if settings.state_file.exists() else None
    records = state.segments if state is not None else {}
    status = {s.segment_id: getattr(records.get(s.segment_id), "status", None) for s in segments}
    completed = {k for k, v in status.items() if v == SegmentStatus.COMPLETED}
    failed = tuple(sorted(k for k, v in status.items() if v == SegmentStatus.ERROR))
    misses = 0
    # The run's language, which the settings need not carry.
    glossary = (
        glossary_for(settings.work_root, settings.work_dir, target_language) if state else None
    )
    if glossary is not None and state is not None:
        from glossary.report import find_misses

        misses = len({m.segment_id for m in find_misses(segments, state, glossary)})
    return RunSummary(
        total=len(segments),
        completed=len(completed),
        failed=failed,
        pending=len(segments) - len(completed) - len(failed),
        translated_now=translated_now,
        seconds=time.monotonic() - started,
        glossary_misses=misses,
    )


def _print_summary(summary: RunSummary, input_epub: Path) -> None:
    minutes, seconds = divmod(int(summary.seconds), 60)
    took = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    # The path as given, quoted for a shell: the bare file name did not work
    # from another directory, nor with a space in it.
    book = escape(shlex.quote(str(input_epub)))
    console.print(
        f"[bold]{summary.completed} of {summary.total} units translated[/bold] "
        f"({summary.translated_now} in this run, {took})."
    )
    if summary.failed:
        console.print(
            f"[red]{len(summary.failed)} failed.[/red] Run `tepub translate {book}` "
            f"again to retry them; `tepub status {book}` lists them."
        )
    if summary.pending:
        console.print(f"[yellow]{summary.pending} still to translate.[/yellow]")
    if summary.glossary_misses:
        console.print(
            f"[yellow]{summary.glossary_misses} unit(s) miss a glossary rendering;[/yellow] "
            f"`tepub glossary check {book}` lists them."
        )


def run_translation(
    settings: AppSettings,
    input_epub: Path,
    *,
    source_language: str,
    target_language: str,
) -> RunSummary:
    """Translate what is left of the book, then report where it stands.

    A run used to end with no summary, so one that left units failed looked
    the same as a clean one, to people and to scripts.
    """
    started = time.monotonic()
    # One run per workspace: a second process used to translate every segment
    # again, and the last to save won. The summary is read under the same lock,
    # so no other command can change the state between the run and its report.
    with exclusive_run(settings.state_file):
        # Counted as the run goes: comparing completed units before and after
        # reported none after a language change reset and retranslated them.
        translated_now = _run_translation(
            settings, input_epub, source_language=source_language, target_language=target_language
        )
        summary = _summarise(settings, translated_now, started, target_language)
    _print_summary(summary, input_epub)
    return summary


@dataclass(frozen=True)
class Plan:
    """What a translate run would do, for --dry-run."""

    units: int
    copied: int
    characters: int
    provider: str
    model: str
    glossary_terms: int


def plan_translation(settings: AppSettings, input_epub: Path) -> Plan:
    """Count what a run would translate, without contacting the provider."""
    segments_doc = load_segments(settings.segments_file)
    assert_same_book(segments_doc, input_epub)
    segments = select_for_translation(segments_doc.segments, settings)
    done: set[str] = set()
    if settings.state_file.exists():
        state = load_state(settings.state_file)
        # A run in other languages resets the finished units and translates them
        # again, so the plan counts them too.
        if _language_codes(state.source_language, state.target_language) == _language_codes(
            settings.source_language, settings.target_language
        ):
            done = {k for k, r in state.segments.items() if r.status == SegmentStatus.COMPLETED}
    remaining = [s for s in segments if s.segment_id not in done]
    todo = [s for s in remaining if not should_auto_copy(s)]
    glossary = glossary_for(settings.work_root, settings.work_dir, settings.target_language)
    return Plan(
        units=len(remaining),
        copied=len(remaining) - len(todo),
        characters=sum(len(s.source_content) for s in todo),
        provider=settings.primary_provider.name,
        model=settings.primary_provider.model,
        glossary_terms=len(glossary.decided()) if glossary is not None else 0,
    )
