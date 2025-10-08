"""Audiobook command implementation."""

import os
import sys
from pathlib import Path

import click

from audiobook import run_audiobook
from audiobook.cover import SpineCoverCandidate, find_spine_cover_candidate
from audiobook.language import detect_language
from audiobook.preprocess import segment_to_text
from audiobook.state import load_state as load_audio_state
from audiobook.voices import format_voice_entry, list_voices_for_provider
from cli.core import prepare_settings_for_epub
from cli.errors import handle_state_errors
from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from epub_io.resources import get_item_by_href
from state.base import safe_load_state
from state.models import SegmentsDocument
from state.store import load_segments

console = get_console()


class HelpfulGroup(click.Group):
    """Command group that shows help instead of error for invalid commands."""

    def resolve_command(self, ctx, args):
        try:
            cmd_name, cmd, args = super().resolve_command(ctx, args)
            return cmd_name, cmd, args
        except click.UsageError:
            # Show help and exit on command not found
            click.echo(self.get_help(ctx))
            ctx.exit(0)


def _write_cover_candidate(
    settings: AppSettings,
    input_epub: Path,
) -> tuple[Path | None, SpineCoverCandidate | None]:
    try:
        reader = EpubReader(input_epub, settings)
    except Exception:
        return None, None
    candidate = find_spine_cover_candidate(reader)
    if not candidate:
        return None, None
    try:
        item = get_item_by_href(reader.book, Path(candidate.href) if isinstance(candidate.href, str) else candidate.href)
    except KeyError:
        return None, None
    cover_dir = settings.work_dir / "audiobook" / "cover_candidates"
    cover_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(candidate.href).suffix or ".img"
    target_name = f"spine_{Path(candidate.href).stem or 'candidate'}{suffix}"
    candidate_path = cover_dir / target_name
    candidate_path.write_bytes(item.get_content())
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


@audiobook.command(name="generate")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option("--voice", default=None, help="Voice name (provider-specific, skip to choose interactively).")
@click.option("--language", default=None, help="Override detected language (e.g. 'en').")
@click.option("--rate", default=None, help="Optional speaking rate override for Edge TTS, e.g. '+5%'.")
@click.option("--volume", default=None, help="Optional volume override for Edge TTS, e.g. '+2dB'.")
@click.option(
    "--tts-provider",
    default=None,
    type=click.Choice(["edge", "openai"], case_sensitive=False),
    help="TTS provider: edge (free, 57+ voices) or openai (paid, 6 premium voices). Default from config.",
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
    help="Speech speed for OpenAI TTS (0.25-4.0, default 1.0).",
)
@click.option(
    "--cover-path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
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

    # Validate that segments file exists (required for audiobook) - errors handled by decorator
    from exceptions import StateFileNotFoundError

    if not settings.segments_file.exists():
        raise StateFileNotFoundError("segments", input_epub)
    # Also validate it's not corrupted
    safe_load_state(settings.segments_file, SegmentsDocument, "segments")

    # Determine TTS provider (from CLI, config, or stored state)
    # Try provider-specific paths first, then fall back to legacy path
    stored_voice = None
    stored_language = None
    stored_cover_path: Path | None = None
    stored_provider = None
    stored_model = None
    stored_speed = None

    # Check for stored state in provider-specific folders
    edge_state_path = settings.work_dir / "audiobook@edgetts" / "audio_state.json"
    openai_state_path = settings.work_dir / "audiobook@openaitts" / "audio_state.json"
    legacy_state_path = settings.work_dir / "audiobook" / "audio_state.json"

    for state_path in [edge_state_path, openai_state_path, legacy_state_path]:
        if state_path.exists():
            try:
                stored_state = load_audio_state(state_path)
                if stored_state:
                    stored_voice = stored_state.session.voice
                    stored_language = stored_state.session.language
                    stored_cover_path = stored_state.session.cover_path
                    stored_provider = stored_state.session.tts_provider
                    stored_model = stored_state.session.tts_model
                    stored_speed = stored_state.session.tts_speed
                    break  # Use the first valid state found
            except Exception:
                continue

    # Determine provider (CLI > config > stored)
    # Config should take precedence over stored state so users can change provider
    selected_provider = (tts_provider or settings.audiobook_tts_provider or stored_provider).lower()
    selected_model = tts_model or settings.audiobook_tts_model or stored_model
    selected_speed = (
        tts_speed if tts_speed is not None
        else (settings.audiobook_tts_speed if settings.audiobook_tts_speed is not None
              else stored_speed)
    )

    # Warn if config changed from stored state provider
    if stored_provider and settings.audiobook_tts_provider != stored_provider and not tts_provider:
        console.print(
            f"[yellow]Note: Provider changed from [bold]{stored_provider}[/bold] to [bold]{settings.audiobook_tts_provider}[/bold] in config. "
            f"Starting fresh with {settings.audiobook_tts_provider}.[/yellow]"
        )

    console.print(f"[cyan]TTS Provider:[/cyan] {selected_provider}")
    if selected_provider == "openai":
        console.print(f"[cyan]Model:[/cyan] {selected_model or 'tts-1'}")
        console.print(f"[cyan]Speed:[/cyan] {selected_speed}")

    segments_doc = load_segments(settings.segments_file)
    sample_texts: list[str] = []
    for segment in segments_doc.segments:
        text_sample = segment_to_text(segment)
        if text_sample:
            sample_texts.append(text_sample)
        if len(sample_texts) >= 50:
            break

    detected_language = language or stored_language or detect_language(sample_texts)
    if not language and detected_language:
        console.print(f"[cyan]Detected language:[/cyan] {detected_language}")

    # Voice selection based on provider
    # Priority: CLI > config > stored (if compatible with provider)
    selected_voice = voice or settings.audiobook_voice
    if not selected_voice:
        # Check if stored voice is compatible with selected provider
        if stored_voice:
            # Edge voices contain hyphens (e.g., en-US-GuyNeural)
            # OpenAI voices are simple names (e.g., alloy, nova)
            stored_is_edge = "-" in stored_voice
            selected_is_edge = selected_provider == "edge"

            # Only use stored voice if provider matches
            if stored_is_edge == selected_is_edge:
                selected_voice = stored_voice

    if not selected_voice:
        # Get voices for the selected provider
        if selected_provider == "edge":
            available_voices = list_voices_for_provider("edge", detected_language)
        else:  # openai
            available_voices = list_voices_for_provider("openai")

        available_voices = sorted(
            available_voices,
            key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")),
        )

        if not available_voices:
            # Fallback based on provider
            if selected_provider == "edge":
                console.print(
                    "[yellow]No voices found for detected language; falling back to en-US-GuyNeural.[/yellow]"
                )
                selected_voice = "en-US-GuyNeural"
            else:  # openai
                console.print("[yellow]Falling back to OpenAI 'alloy' voice.[/yellow]")
                selected_voice = "alloy"
        else:
            if selected_voice and any(v.get("ShortName") == selected_voice for v in available_voices):
                # Voice was already set from stored state (and validated as compatible)
                console.print(f"[cyan]Using stored voice:[/cyan] {selected_voice}")
            elif sys.stdin.isatty():
                click.echo(f"\nSelect a {selected_provider.upper()} voice:")
                default_choice = 1
                for idx, voice_info in enumerate(available_voices, start=1):
                    click.echo(f"  {idx}. {format_voice_entry(voice_info, selected_provider)}")
                    if stored_voice and voice_info.get("ShortName") == stored_voice:
                        default_choice = idx
                choice = click.prompt(
                    "Voice number",
                    default=default_choice,
                    type=click.IntRange(1, len(available_voices)),
                )
                selected_voice = available_voices[choice - 1]["ShortName"]
                console.print(f"[cyan]Using voice:[/cyan] {selected_voice}")
            else:
                selected_voice = available_voices[0]["ShortName"]
                console.print(f"[cyan]Using voice:[/cyan] {selected_voice}")
    else:
        if stored_voice and selected_voice == stored_voice and not voice:
            console.print(f"[cyan]Using stored voice:[/cyan] {selected_voice}")

    if not selected_voice:
        selected_voice = "alloy" if selected_provider == "openai" else "en-US-GuyNeural"

    env_cover_value = os.environ.get("TEPUB_AUDIOBOOK_COVER_PATH")
    env_cover_path = None
    if env_cover_value:
        env_cover_path = Path(env_cover_value).expanduser()
        if not env_cover_path.exists():
            raise click.UsageError(
                f"Cover path from TEPUB_AUDIOBOOK_COVER_PATH does not exist: {env_cover_path}"
            )

    # Priority: CLI > env > config > stored
    selected_cover_path = cover_path or env_cover_path or settings.cover_image_path

    # Handle config cover path (may be relative)
    if selected_cover_path and not (cover_path or env_cover_path):
        # This is from config, convert to Path if needed and resolve relative paths
        if not isinstance(selected_cover_path, Path):
            selected_cover_path = Path(selected_cover_path)

        if not selected_cover_path.is_absolute():
            selected_cover_path = settings.work_dir / selected_cover_path
        selected_cover_path = selected_cover_path.expanduser()

        if not selected_cover_path.exists():
            console.print(
                f"[yellow]Config cover_image_path not found: {selected_cover_path}. Falling back to auto-detection.[/yellow]"
            )
            selected_cover_path = None
        else:
            console.print(f"[cyan]Using config cover:[/cyan] {selected_cover_path}")

    if selected_cover_path and not selected_cover_path.exists():
        raise click.UsageError(f"Cover path does not exist: {selected_cover_path}")

    candidate_path: Path | None = None
    candidate_info: SpineCoverCandidate | None = None

    if selected_cover_path is None:
        if stored_cover_path:
            stored_cover_fs = Path(stored_cover_path)
            if stored_cover_fs.exists():
                selected_cover_path = stored_cover_fs
                console.print(f"[cyan]Using stored cover:[/cyan] {stored_cover_fs}")
            else:
                console.print(
                    f"[yellow]Stored cover path no longer exists; ignoring {stored_cover_path}.[/yellow]"
                )

    if selected_cover_path is None:
        candidate_path, candidate_info = _write_cover_candidate(settings, input_epub)
        if candidate_path and candidate_info:
            click.echo(
                f"\nDetected cover candidate: {candidate_info.href} (from {candidate_info.document_href})"
            )
            click.echo(f"Extracted candidate to: {candidate_path}")
            if sys.stdin.isatty():
                choice = click.prompt(
                    "Cover selection",
                    type=click.Choice(["use", "manual", "skip"], case_sensitive=False),
                    default="use",
                )
                choice = choice.lower()
                if choice == "use":
                    selected_cover_path = candidate_path
                elif choice == "manual":
                    selected_cover_path = click.prompt(
                        "Enter path to cover image",
                        type=click.Path(exists=True, path_type=Path),
                    )
                else:
                    selected_cover_path = None
            else:
                selected_cover_path = candidate_path
                console.print(
                    "[cyan]Non-interactive run; using detected cover candidate automatically.[/cyan]"
                )
        elif sys.stdin.isatty():
            if click.confirm(
                "No cover candidate detected automatically. Specify a cover image?", default=False
            ):
                selected_cover_path = click.prompt(
                    "Enter path to cover image",
                    type=click.Path(exists=True, path_type=Path),
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

        console.print(f"[cyan]Extracting chapter structure from EPUB...[/cyan]")
        chapters, metadata = extract_chapters_from_epub(source, settings)

        # Default output to work_dir/chapters.yaml
        if output is None:
            output = settings.work_dir / "chapters.yaml"

    elif source.suffix.lower() in {".m4a", ".mp4"}:
        # Extract from audiobook
        console.print(f"[cyan]Reading chapter markers from audiobook...[/cyan]")
        chapters, metadata = extract_chapters_from_mp4(source)

        # Default output to source directory
        if output is None:
            output = source.parent / "chapters.yaml"

    else:
        raise click.UsageError(
            f"Unsupported file type: {source.suffix}. Expected .epub or .m4a"
        )

    # Write YAML
    write_chapters_yaml(chapters, metadata, output)

    console.print(f"\n[green]✓ Exported {len(chapters)} chapters to:[/green] {output}")
    console.print(f"\n[cyan]Edit the file to customize chapter titles/timestamps, then use:[/cyan]")
    if source.suffix.lower() == ".epub":
        console.print(f"  tepub audiobook {source.name}")
        console.print(f"[dim](Audiobook generation will use custom titles from chapters.yaml)[/dim]")
    else:
        console.print(f"  tepub audiobook update-chapters {source.name} chapters.yaml")


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
    console.print(f"[cyan]Loading chapter configuration from:[/cyan] {chapters_file}")
    chapters, metadata = read_chapters_yaml(chapters_file)

    console.print(f"[cyan]Found {len(chapters)} chapters[/cyan]")

    # Update audiobook
    console.print(f"[cyan]Updating chapter markers in:[/cyan] {audiobook_file}")
    update_mp4_chapters(audiobook_file, chapters)

    console.print(f"\n[green]✓ Successfully updated {len(chapters)} chapter markers[/green]")
    console.print(f"\n[dim]Chapters:[/dim]")
    for i, ch in enumerate(chapters[:5], 1):  # Show first 5
        start_time = f"{ch.start:.1f}s" if ch.start is not None else "N/A"
        console.print(f"  {i}. {start_time:>8} - {ch.title}")
    if len(chapters) > 5:
        console.print(f"  ... and {len(chapters) - 5} more")
