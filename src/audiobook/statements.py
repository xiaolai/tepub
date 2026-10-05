"""Opening and closing statements: wording, synthesis, and their cache."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pydub import AudioSegment

from config.placeholders import fill_placeholders
from console_singleton import get_console

from .models import AudioSessionConfig

logger = logging.getLogger(__name__)
console = get_console()



def _extract_narrator_name(voice_id: str) -> str:
    """Extract friendly narrator name from voice ID.

    Examples:
        en-US-GuyNeural -> Guy
        en-US-JennyNeural -> Jenny
        alloy -> alloy
    """
    # Remove language prefix (e.g., "en-US-")
    parts = voice_id.split("-")
    if len(parts) >= 3:
        name_part = "-".join(parts[2:])
    else:
        name_part = voice_id

    # Remove common suffixes
    for suffix in ["Neural", "Multilingual", "Turbo"]:
        if name_part.endswith(suffix):
            name_part = name_part[:-len(suffix)]

    # Clean up any remaining hyphens or underscores
    name = name_part.strip("-_")

    return name if name else voice_id

def _generate_statement_audio(
    text: str,
    session: AudioSessionConfig,
    output_path: Path,
) -> Path | None:
    """Generate audio for opening/closing statement using the configured TTS engine.

    Matches the renderer.py workflow: generate TTS output, then convert to M4A.

    Args:
        text: Statement text to synthesize
        session: Audio session config with TTS provider and voice settings
        output_path: Where to save the M4A audio file

    Returns:
        Path to the generated M4A file, or None when the text is empty.

    Raises:
        Any error from the TTS engine or the conversion; callers decide.
    """
    if not text or not text.strip():
        return None

    # The output folder may not exist yet: statements are rendered before any
    # chapter is written into it.
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use the same TTS engine as the main audiobook
    from .tts import create_tts_engine

    engine = create_tts_engine(
        provider=session.tts_provider,
        voice=session.voice,
        rate=None,  # Edge TTS only
        volume=None,  # Edge TTS only
        model=session.tts_model,
        speed=session.tts_speed,
    )

    # Determine temp file extension based on provider
    # OpenAI outputs AAC, Edge outputs MP3
    temp_ext = ".aac" if session.tts_provider == "openai" else ".mp3"
    temp_file = output_path.with_suffix(temp_ext)

    # Generate TTS output
    engine.synthesize(text.strip(), temp_file)

    # Always convert to M4A (matching renderer.py approach)
    audio = AudioSegment.from_file(temp_file)
    audio.export(
        output_path,
        format="mp4",
        codec="aac",
        parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
    )
    temp_file.unlink()  # Remove temporary file
    return output_path

def _render_statement(
    label: str,
    template: str | None,
    session: AudioSessionConfig,
    output_root: Path,
    book_title: str,
    author_str: str,
) -> Path | None:
    """Render one opening/closing statement to audio.

    The opening and closing paths were near-identical copies differing only in
    which setting they read and which words they logged.
    """
    if not template:
        return None

    # Only {book_name}, {author} and {narrator_name} are filled; any other brace
    # is text (see config.placeholders).
    text = fill_placeholders(
        template,
        {
            "book_name": book_title,
            "author": author_str,
            "narrator_name": _extract_narrator_name(session.voice),
        },
    )
    if not text.strip():
        return None
    # Cached under everything that shapes the audio. It used to be synthesised
    # on every assembly, a cover-only rebuild included, and deleted afterwards,
    # so a paid voice was paid again each time.
    key = hashlib.sha256(
        json.dumps(
            [text, session.tts_provider, session.voice, session.tts_model,
             session.tts_speed, session.rate, session.volume]
        ).encode("utf-8")
    ).hexdigest()[:16]
    audio_path = output_root / "statements" / f"{label}-{key}.m4a"
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    partial = audio_path.with_name(audio_path.stem + ".partial.m4a")
    # A configured statement that cannot be rendered stops assembly. Logging and
    # carrying on produced a book silently missing its opening or closing.
    try:
        rendered = _generate_statement_audio(text, session, partial)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not render the {label} statement: {exc}") from exc
    if rendered is None:
        return None
    partial.replace(audio_path)
    console.print(f"[cyan]Generated {label} statement audio[/cyan]")
    return audio_path
