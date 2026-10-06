"""Audiobook command implementation."""

import math
import os
import sys
from pathlib import Path

import click
import yaml
from mutagen import MutagenError
from PIL import Image
from rich.markup import escape

from audiobook import run_audiobook
from audiobook.cover import SpineCoverCandidate, find_spine_cover_candidate
from audiobook.language import detect_language
from audiobook.models import AudioSessionConfig
from audiobook.preprocess import segment_to_text
from audiobook.selection import audiobook_segments
from audiobook.state import load_state as load_audio_state
from audiobook.tts import InvalidProsodyError, check_prosody
from audiobook.voices import format_voice_entry, list_voices_for_provider
from cli.core import prepare_settings_for_epub
from cli.errors import handle_state_errors
from config import AppSettings
from config.workspace import assert_same_book
from console_singleton import get_console
from epub_io.reader import EpubReader
from exceptions import CorruptedStateError
from state.base import safe_load_state
from state.models import SegmentsDocument

console = get_console()


class HelpfulGroup(click.Group):
    """Command group that shows help instead of error for invalid commands."""

    def resolve_command(self, ctx, args):
        try:
            cmd_name, cmd, args = super().resolve_command(ctx, args)
            return cmd_name, cmd, args
        except click.UsageError:
            # Show help, but exit non-zero: an unknown subcommand is a usage error,
            # and exiting 0 made scripts treat a typo as a successful run.
            click.echo(self.get_help(ctx))
            ctx.exit(2)


def _write_cover_candidate(
    settings: AppSettings,
    input_epub: Path,
) -> tuple[Path | None, SpineCoverCandidate | None]:
    try:
        reader = EpubReader(input_epub, settings)
    except (OSError, ValueError, KeyError) as exc:
        # Reporting every failure as "no cover candidate" concealed unreadable or
        # corrupt EPUBs, which look identical to a book that simply has no cover.
        console.print(
            f"[yellow]Could not read {escape(input_epub.name)} for cover art: "
            f"{escape(str(exc))}[/yellow]"
        )
        return None, None
    candidate = find_spine_cover_candidate(reader)
    if not candidate:
        return None, None
    item = reader.item_by_href(Path(candidate.href))
    if item is None:
        return None, None
    cover_dir = settings.work_dir / "audiobook" / "cover_candidates"
    cover_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(candidate.href).suffix or ".img"
    target_name = f"spine_{Path(candidate.href).stem or 'candidate'}{suffix}"
    candidate_path = cover_dir / target_name
    candidate_path.write_bytes(reader.read_bytes(item))
    return candidate_path, candidate


@click.group(cls=HelpfulGroup, invoke_without_command=True)
@click.pass_context
def audiobook(ctx: click.Context) -> None:
    """Audiobook generation and chapter management.

    Supports two TTS providers:
    - Edge TTS (default): Free, 57+ voices, no API key needed
    - OpenAI TTS: Paid (~$15/1M chars), 6 premium voices, requires OPENAI_API_KEY

    Subcommands:
    - generate: Create audiobook from EPUB file
    - export-chapters: Export chapter structure to YAML config
    - update-chapters: Update audiobook with new chapter markers from YAML
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


def _prosody(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    try:
        return check_prosody(param.name or "value", value)
    except InvalidProsodyError as exc:
        raise click.BadParameter(str(exc)) from exc


def _speed(ctx: click.Context, param: click.Parameter, value: float | None) -> float | None:
    if value is not None and not (math.isfinite(value) and 0.25 <= value <= 4.0):
        raise click.BadParameter(f"must be between 0.25 and 4.0, got {value}.")
    return value


def _image_problem(path: Path) -> str | None:
    """Why ``path`` cannot serve as a cover, or None if it can."""
    if not path.is_file():
        return "is not a file"
    try:
        # Decode every pixel: verify() reads only the headers and passed
        # truncated JPEGs that then failed when the cover was embedded.
        with Image.open(path) as image:
            image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        return f"is not a readable image ({exc})"
    return None


def _require_image(path: Path, source: str) -> Path:
    """Stop unless ``path`` is an image file; a bad cover used to vanish silently."""
    problem = _image_problem(path)
    if problem:
        raise click.UsageError(f"Cover from {source} {problem}: {path}")
    return path


def _voice_fits(voice: str, provider: str) -> bool:
    # Edge voices contain hyphens (en-US-GuyNeural); OpenAI voices are single
    # names (alloy, nova).
    return ("-" in voice) == (provider == "edge")


def _stored_session(settings: AppSettings, provider: str) -> AudioSessionConfig | None:
    """The session of an earlier run, searching the selected provider's first.

    The order was always Edge-then-OpenAI, so choosing OpenAI while an Edge
    workspace existed loaded Edge's voice, model and speed.
    """
    edge_state_path = settings.work_dir / "audiobook@edgetts" / "audio_state.json"
    openai_state_path = settings.work_dir / "audiobook@openaitts" / "audio_state.json"
    legacy_state_path = settings.work_dir / "audiobook" / "audio_state.json"
    if provider == "openai":
        search_order = [openai_state_path, edge_state_path, legacy_state_path]
    else:
        search_order = [edge_state_path, openai_state_path, legacy_state_path]

    for state_path in search_order:
        if not state_path.exists():
            continue
        try:
            return load_audio_state(state_path).session
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if state_path == search_order[0]:
                # This run resumes this very file; ignoring it here only moved
                # the failure into the run.
                raise CorruptedStateError(state_path, "audiobook", str(exc)) from exc
            console.print(
                f"[yellow]Ignoring unreadable audio state at {escape(str(state_path))}: "
                f"{escape(str(exc))}[/yellow]"
            )
    return None


def _sample_texts(settings: AppSettings, segments_doc: SegmentsDocument) -> list[str]:
    """Text for language detection, from the segments the audiobook will speak."""
    selected = sorted(
        audiobook_segments(settings, segments_doc.segments),
        key=lambda seg: (seg.metadata.spine_index, seg.metadata.order_in_file),
    )
    sample_texts: list[str] = []
    for segment in selected:
        text_sample = segment_to_text(segment)
        if text_sample:
            sample_texts.append(text_sample)
        if len(sample_texts) >= 50:
            break
    return sample_texts


def _choose_voice(
    voice: str | None,
    settings: AppSettings,
    stored_voice: str | None,
    provider: str,
    language: str | None,
) -> str:
    """CLI > config > stored (if it suits the provider) > ask."""
    chosen = voice or settings.audiobook_voice
    if chosen:
        if not _voice_fits(chosen, provider):
            source = "--voice" if voice else "audiobook_voice"
            raise click.UsageError(
                f"Voice {chosen!r} from {source} is not a {provider} voice. "
                f"Pass a voice for {provider}, or choose the matching --tts-provider."
            )
        if stored_voice and chosen == stored_voice and not voice:
            console.print(f"[cyan]Using stored voice:[/cyan] {escape(str(chosen))}")
        return chosen

    if stored_voice and _voice_fits(stored_voice, provider):
        return stored_voice

    if provider == "edge":
        available_voices = list_voices_for_provider("edge", language)
    else:
        available_voices = list_voices_for_provider("openai")
    available_voices = sorted(
        available_voices,
        key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")),
    )

    if not available_voices:
        # Falling back to an English voice read a book in another language
        # with the wrong voice, unasked.
        raise click.UsageError(
            f"No {provider} voices found for language {language!r}. "
            "Pass --voice, or set audiobook_voice in the config."
        )
    if not sys.stdin.isatty():
        # With no terminal to ask in, the first voice in a sorted list was
        # taken: an Australian voice for an American book, chosen silently
        # before hours of synthesis. Stop and say how to choose.
        examples = ", ".join(v["ShortName"] for v in available_voices[:4])
        raise click.UsageError(
            "No voice chosen and no terminal to ask in. Pass --voice, or set "
            f"audiobook_voice in the config; voices for this book include {examples}."
        )

    click.echo(f"\nSelect a {provider.upper()} voice:")
    for idx, voice_info in enumerate(available_voices, start=1):
        click.echo(f"  {idx}. {format_voice_entry(voice_info, provider)}")
    choice = click.prompt(
        "Voice number",
        default=1,
        type=click.IntRange(1, len(available_voices)),
    )
    chosen = available_voices[choice - 1]["ShortName"]
    console.print(f"[cyan]Using voice:[/cyan] {escape(str(chosen))}")
    return chosen


def _choose_cover(
    cover_path: Path | None,
    settings: AppSettings,
    stored_cover_path: Path | None,
    input_epub: Path,
) -> Path | None:
    """CLI > env > config > stored > detected candidate > ask."""
    if cover_path is not None:
        return _require_image(cover_path, "--cover-path")

    # Only the source actually used is checked: a stale environment variable
    # used to reject a run that passed a valid --cover-path.
    env_cover_value = os.environ.get("TEPUB_AUDIOBOOK_COVER_PATH")
    if env_cover_value:
        env_cover_path = Path(env_cover_value).expanduser()
        if not env_cover_path.exists():
            raise click.UsageError(
                f"Cover path from TEPUB_AUDIOBOOK_COVER_PATH does not exist: {env_cover_path}"
            )
        return _require_image(env_cover_path, "TEPUB_AUDIOBOOK_COVER_PATH")

    if settings.cover_image_path:
        # Expanded first: "~/cover.jpg" is not absolute until it is.
        config_cover = Path(settings.cover_image_path).expanduser()
        if not config_cover.is_absolute():
            config_cover = settings.work_dir / config_cover
        if config_cover.exists():
            console.print(f"[cyan]Using config cover:[/cyan] {escape(str(config_cover))}")
            return _require_image(config_cover, "cover_image_path")
        console.print(
            f"[yellow]Config cover_image_path not found: {escape(str(config_cover))}. "
            "Falling back to auto-detection.[/yellow]"
        )

    if stored_cover_path:
        stored_cover_fs = Path(stored_cover_path)
        if stored_cover_fs.exists():
            # The run would reuse it from the session anyway, so it is checked
            # here rather than dropped once the audio is already made.
            _require_image(stored_cover_fs, "the previous run (pass --cover-path to replace it)")
            console.print(f"[cyan]Using stored cover:[/cyan] {escape(str(stored_cover_fs))}")
            return stored_cover_fs
        console.print(
            "[yellow]Stored cover path no longer exists; ignoring "
            f"{escape(str(stored_cover_path))}.[/yellow]"
        )

    image_path = click.Path(exists=True, dir_okay=False, path_type=Path)
    candidate_path, candidate_info = _write_cover_candidate(settings, input_epub)
    if candidate_path and candidate_info:
        problem = _image_problem(candidate_path)
        if problem:
            # A detected image is only a guess (SVG covers are common), so an
            # unusable one is reported and passed over, not made the cover.
            console.print(
                f"[yellow]Detected cover candidate {escape(str(candidate_info.href))} "
                f"{escape(str(problem))}; "
                "not using it.[/yellow]"
            )
            candidate_path = candidate_info = None
    if candidate_path and candidate_info:
        click.echo(
            f"\nDetected cover candidate: {candidate_info.href} "
            f"(from {candidate_info.document_href})"
        )
        click.echo(f"Extracted candidate to: {candidate_path}")
        if not sys.stdin.isatty():
            console.print(
                "[cyan]Non-interactive run; using detected cover candidate automatically.[/cyan]"
            )
            return candidate_path
        choice = click.prompt(
            "Cover selection",
            type=click.Choice(["use", "manual", "skip"], case_sensitive=False),
            default="use",
        ).lower()
        if choice == "use":
            return candidate_path
        if choice == "manual":
            return _require_image(
                click.prompt("Enter path to cover image", type=image_path), "the prompt"
            )
        return None
    if sys.stdin.isatty() and click.confirm(
        "No cover candidate detected automatically. Specify a cover image?", default=False
    ):
        return _require_image(
            click.prompt("Enter path to cover image", type=image_path), "the prompt"
        )
    return None


@audiobook.command(name="generate")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--voice",
    default=None,
    help="Voice name (provider-specific, skip to choose interactively).",
)
@click.option("--language", default=None, help="Override detected language (e.g. 'en').")
@click.option(
    "--rate",
    default=None,
    callback=_prosody,
    help="Optional speaking rate override for Edge TTS, e.g. '+5%'.",
)
@click.option(
    "--volume",
    default=None,
    callback=_prosody,
    help="Optional volume override for Edge TTS, e.g. '+2%'.",
)
@click.option(
    "--tts-provider",
    default=None,
    type=click.Choice(["edge", "openai"], case_sensitive=False),
    help=(
        "TTS provider: edge (free, 57+ voices) or openai (paid, 6 premium voices). "
        "Default from config."
    ),
)
@click.option(
    "--tts-model",
    default=None,
    help="TTS model for OpenAI: tts-1 (cheaper) or tts-1-hd (higher quality).",
)
@click.option(
    "--tts-speed",
    default=None,
    type=float,
    callback=_speed,
    help="Speech speed for OpenAI TTS (0.25-4.0, default 1.0).",
)
@click.option(
    "--cover-path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Optional path to an image file to embed as the audiobook cover.",
)
@click.option(
    "--cover-only",
    is_flag=True,
    help="Skip synthesis and only rebuild the audiobook container with the selected cover.",
)
@click.pass_context
@handle_state_errors
def generate(
    ctx: click.Context,
    input_epub: Path,
    voice: str | None,
    rate: str | None,
    volume: str | None,
    language: str | None,
    tts_provider: str | None,
    tts_model: str | None,
    tts_speed: float | None,
    cover_path: Path | None,
    cover_only: bool,
) -> None:
    """Generate an audiobook from EPUB file using TTS.

    INPUT_EPUB: Path to the EPUB file to convert to audiobook.

    Examples:
      tepub audiobook generate book.epub
      tepub audiobook generate book.epub --tts-provider openai --voice nova
      tepub audiobook generate book.epub --voice en-US-GuyNeural --rate '+10%'
    """
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)

    if cover_only:
        # A cover rebuild reuses the existing audio; any of these would change it.
        overrides = [
            name
            for name, value in (
                ("--voice", voice),
                ("--language", language),
                ("--rate", rate),
                ("--volume", volume),
                ("--tts-model", tts_model),
                ("--tts-speed", tts_speed),
            )
            if value is not None
        ]
        if overrides:
            raise click.UsageError(
                f"--cover-only reuses the existing audio, so {', '.join(overrides)} "
                "cannot apply. Drop them, or run without --cover-only."
            )

    # Validate that segments file exists (required for audiobook) - errors handled by decorator
    from exceptions import StateFileNotFoundError

    if not settings.segments_file.exists():
        raise StateFileNotFoundError("segments", input_epub)
    # Also validate it's not corrupted, and that it is this book's: another
    # book's workspace would be narrated under this book's title.
    segments_doc = safe_load_state(settings.segments_file, SegmentsDocument, "segments")
    assert_same_book(segments_doc, input_epub)

    preferred_provider = (tts_provider or settings.audiobook_tts_provider or "").lower()
    stored = _stored_session(settings, preferred_provider)
    stored_provider = stored.tts_provider if stored else None

    # Determine provider (CLI > config > stored)
    # Config should take precedence over stored state so users can change provider
    selected_provider = (tts_provider or settings.audiobook_tts_provider or stored_provider).lower()
    selected_model = (
        tts_model or settings.audiobook_tts_model or (stored.tts_model if stored else None)
    )
    selected_speed = (
        tts_speed if tts_speed is not None
        else (settings.audiobook_tts_speed if settings.audiobook_tts_speed is not None
              else (stored.tts_speed if stored else None))
    )

    # Warn if config changed from stored state provider
    if stored_provider and settings.audiobook_tts_provider != stored_provider and not tts_provider:
        console.print(
            f"[yellow]Note: Provider changed from [bold]{escape(str(stored_provider))}[/bold] to "
            f"[bold]{escape(str(settings.audiobook_tts_provider))}[/bold] in config. "
            f"Starting fresh with {escape(str(settings.audiobook_tts_provider))}.[/yellow]"
        )

    console.print(f"[cyan]TTS Provider:[/cyan] {escape(str(selected_provider))}")
    if selected_provider == "openai":
        console.print(f"[cyan]Model:[/cyan] {escape(str(selected_model or 'tts-1'))}")
        # None: neither chosen nor stored, so the session default applies.
        shown_speed = (
            selected_speed if selected_speed is not None
            else AudioSessionConfig.model_fields["tts_speed"].default
        )
        console.print(f"[cyan]Speed:[/cyan] {shown_speed}")

    if cover_only:
        # The runner keeps the stored session; voice and language are not used.
        detected_language = stored.language if stored else None
        selected_voice = stored.voice if stored else ""
    else:
        detected_language = (
            language
            or (stored.language if stored else None)
            or detect_language(_sample_texts(settings, segments_doc))
        )
        if not language and detected_language:
            console.print(f"[cyan]Detected language:[/cyan] {escape(str(detected_language))}")
        selected_voice = _choose_voice(
            voice, settings, stored.voice if stored else None, selected_provider, detected_language
        )

    selected_cover_path = _choose_cover(
        cover_path, settings, stored.cover_path if stored else None, input_epub
    )

    run_audiobook(
        settings=settings,
        input_epub=input_epub,
        voice=selected_voice,
        language=detected_language,
        rate=rate,
        volume=volume,
        cover_path=selected_cover_path,
        cover_only=cover_only,
        tts_provider=selected_provider,
        tts_model=selected_model,
        tts_speed=selected_speed,
    )


@audiobook.command(name="export-chapters")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Output YAML file path (default: chapters.yaml in work_dir)",
)
@click.pass_context
def export_chapters(ctx: click.Context, source: Path, output: Path | None) -> None:
    """Export chapter information to YAML config file.

    SOURCE can be either:
    - EPUB file (.epub): Extract chapter structure before generation (preview mode)
    - M4A audiobook (.m4a): Extract chapter markers from existing audiobook

    The exported YAML file can be edited and used with update-chapters command.
    """
    from audiobook.chapters import (
        extract_chapters_from_epub,
        extract_chapters_from_mp4,
        write_chapters_yaml,
    )

    settings: AppSettings = ctx.obj["settings"]

    # Determine source type
    if source.suffix.lower() == ".epub":
        # Preview mode: extract from EPUB
        settings = prepare_settings_for_epub(ctx, settings, source, override=None)

        # Validate segments file exists
        from exceptions import StateFileNotFoundError

        if not settings.segments_file.exists():
            raise StateFileNotFoundError("segments", source)

        console.print("[cyan]Extracting chapter structure from EPUB...[/cyan]")
        chapters, metadata = extract_chapters_from_epub(source, settings)

        # Default output to work_dir/chapters.yaml
        if output is None:
            output = settings.work_dir / "chapters.yaml"

    elif source.suffix.lower() in {".m4a", ".mp4"}:
        # Extract from audiobook
        console.print("[cyan]Reading chapter markers from audiobook...[/cyan]")
        try:
            chapters, metadata = extract_chapters_from_mp4(source)
        except (ValueError, MutagenError) as exc:
            raise click.ClickException(f"Cannot read chapters from {source}: {exc}") from exc

        # Default output to source directory
        if output is None:
            output = source.parent / "chapters.yaml"

    else:
        raise click.UsageError(
            f"Unsupported file type: {source.suffix}. Expected .epub or .m4a"
        )

    # Write YAML
    write_chapters_yaml(chapters, metadata, output)

    console.print(f"\n[green]✓ Exported {len(chapters)} chapters to:[/green] {escape(str(output))}")
    console.print("\n[cyan]Edit the file to customize chapter titles/timestamps, then use:[/cyan]")
    if source.suffix.lower() == ".epub":
        console.print(f"  tepub audiobook generate {escape(source.name)}")
        console.print("[dim](Audiobook generation will use custom titles from chapters.yaml)[/dim]")
    else:
        console.print(f"  tepub audiobook update-chapters {escape(source.name)} chapters.yaml")


@audiobook.command(name="update-chapters")
@click.argument("audiobook_file", type=click.Path(exists=True, path_type=Path))
@click.argument("chapters_file", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def update_chapters(ctx: click.Context, audiobook_file: Path, chapters_file: Path) -> None:
    """Update M4A audiobook with chapter markers from YAML config.

    AUDIOBOOK_FILE: Path to M4A audiobook file
    CHAPTERS_FILE: Path to YAML config file with chapter information

    This command updates the chapter markers in an existing audiobook file.
    All chapters in the YAML must have timestamps.
    """
    from audiobook.chapters import read_chapters_yaml, update_mp4_chapters

    # Validate audiobook file
    if audiobook_file.suffix.lower() not in {".m4a", ".mp4"}:
        raise click.UsageError(
            f"Unsupported audiobook format: {audiobook_file.suffix}. Expected .m4a or .mp4"
        )

    # Read chapters from YAML
    console.print(f"[cyan]Loading chapter configuration from:[/cyan] {escape(str(chapters_file))}")
    # Expected input mistakes are reported as such, not as a traceback.
    try:
        chapters, _ = read_chapters_yaml(chapters_file)
    except (yaml.YAMLError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(f"Invalid chapters file {chapters_file}: {exc}") from exc

    console.print(f"[cyan]Found {len(chapters)} chapters[/cyan]")

    # Update audiobook
    console.print(f"[cyan]Updating chapter markers in:[/cyan] {escape(str(audiobook_file))}")
    try:
        update_mp4_chapters(audiobook_file, chapters)
    except (ValueError, MutagenError) as exc:
        raise click.ClickException(f"Cannot update {audiobook_file}: {exc}") from exc

    console.print(f"\n[green]✓ Successfully updated {len(chapters)} chapter markers[/green]")
    console.print("\n[dim]Chapters:[/dim]")
    for i, ch in enumerate(chapters[:5], 1):  # Show first 5
        start_time = f"{ch.start:.1f}s" if ch.start is not None else "N/A"
        console.print(f"  {i}. {start_time:>8} - {escape(str(ch.title))}")
    if len(chapters) > 5:
        console.print(f"  ... and {len(chapters) - 5} more")
