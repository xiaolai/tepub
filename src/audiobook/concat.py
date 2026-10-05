"""Join AAC audio files with ffmpeg, and check that the result is what was asked.

Every concatenation in the audiobook goes through ``concat_audio``. It exists
because the hand-written calls it replaces reported success on wrong audio:

- Silence made by pydub defaulted to 11025 Hz and was stream-copied beside
  24 kHz speech. A 3 s pause played as about 1.4 s, and chapter markers, which
  are computed from container durations, drifted late.
- An apostrophe in a path ended a concat-list entry early. ffmpeg then dropped
  the remaining inputs and still exited 0.

So inputs must share one format, silence is generated in the format of the
speech beside it, every path is escaped, and the output's duration must equal
the sum of its inputs.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Every AAC file tepub writes uses this movie timescale; keep silence identical
# so the copied streams agree.
MOVIE_TIMESCALE = "24000"

# Per-input allowance for AAC frame rounding when durations are summed.
_PER_INPUT_TOLERANCE_S = 0.05
_BASE_TOLERANCE_S = 0.1

_CHANNEL_LAYOUTS = {1: "mono", 2: "stereo"}


class ConcatError(RuntimeError):
    """Concatenation failed, or produced audio that does not match its inputs."""


@dataclass(frozen=True)
class AudioFormat:
    codec: str
    sample_rate: int
    channels: int


def _run(command: list[str], *, what: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ConcatError(f"{command[0]} is not installed; it is needed to {what}") from exc
    except subprocess.CalledProcessError as exc:
        tail = "\n".join((exc.stderr or "").strip().splitlines()[-8:])
        raise ConcatError(f"Could not {what}:\n{tail}") from exc


def audio_format(path: Path) -> AudioFormat:
    out = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels",
            "-of", "csv=p=0", str(path),
        ],
        what=f"read the audio format of {path}",
    ).stdout.strip()
    try:
        codec, rate, channels = out.split(",")[:3]
        return AudioFormat(codec, int(rate), int(channels))
    except ValueError as exc:
        raise ConcatError(f"{path} has no readable audio stream (ffprobe said {out!r})") from exc


def container_duration(path: Path) -> float:
    out = _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        what=f"read the duration of {path}",
    ).stdout.strip()
    try:
        return float(out)
    except ValueError as exc:
        raise ConcatError(f"{path} reports no duration (ffprobe said {out!r})") from exc


def decoded_duration(path: Path) -> float:
    """Duration measured by decoding every sample, not by trusting the container.

    Slow for a whole book; meant for tests and diagnosis.
    """
    stderr = _run(
        ["ffmpeg", "-v", "info", "-nostats", "-i", str(path), "-f", "null", "-"],
        what=f"decode {path}",
    ).stderr
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not times:
        raise ConcatError(f"ffmpeg reported no decoded time for {path}")
    hours, minutes, seconds = times[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def write_silence(path: Path, seconds: float, *, like: Path) -> Path:
    """Write ``seconds`` of AAC silence in the same format as ``like``."""
    if seconds <= 0:
        raise ValueError(f"silence must be longer than zero seconds, got {seconds}")
    fmt = audio_format(like)
    layout = _CHANNEL_LAYOUTS.get(fmt.channels)
    if layout is None:
        raise ConcatError(f"{like} has {fmt.channels} channels; only mono and stereo are supported")
    path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r={fmt.sample_rate}:cl={layout}",
            "-t", f"{seconds:.3f}",
            "-c:a", "aac", "-movflags", "+faststart", "-movie_timescale", MOVIE_TIMESCALE,
            str(path),
        ],
        what=f"write {seconds:.2f} s of silence to {path}",
    )
    return path


def _concat_entry(path: Path) -> str:
    # The demuxer ends a quoted string at a single quote; the documented escape
    # closes the quote, emits an escaped quote, and reopens it.
    escaped = str(path.absolute()).replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _concat_list(paths: list[Path]) -> str:
    return "".join(_concat_entry(path) for path in paths)


def concat_audio(inputs: list[Path], output: Path) -> Path:
    """Join ``inputs`` into ``output`` without re-encoding, and verify the result."""
    if not inputs:
        raise ConcatError(f"nothing to concatenate into {output}")

    formats = {path: audio_format(path) for path in inputs}
    first_path, first = next(iter(formats.items()))
    for path, fmt in formats.items():
        if fmt.sample_rate != first.sample_rate:
            raise ConcatError(
                f"{path.name} has sample rate {fmt.sample_rate} Hz but {first_path.name} has "
                f"{first.sample_rate} Hz; stream-copying them together corrupts the timing"
            )
        if fmt.channels != first.channels or fmt.codec != first.codec:
            raise ConcatError(
                f"{path.name} is {fmt.codec}/{fmt.channels} ch but {first_path.name} is "
                f"{first.codec}/{first.channels} ch; they cannot be stream-copied together"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="concat-", dir=output.parent, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(_concat_list(list(inputs)))
        list_path = Path(handle.name)
    try:
        _run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-vn", "-c:a", "copy", str(output),
            ],
            what=f"concatenate {len(inputs)} files into {output}",
        )
    finally:
        list_path.unlink(missing_ok=True)

    expected = sum(container_duration(path) for path in inputs)
    actual = container_duration(output)
    tolerance = _BASE_TOLERANCE_S + _PER_INPUT_TOLERANCE_S * len(inputs)
    if abs(actual - expected) > tolerance:
        raise ConcatError(
            f"{output.name} is {actual:.1f} s long but its {len(inputs)} inputs add up to "
            f"{expected:.1f} s; expected about {expected:.1f} s. Some inputs were dropped."
        )
    return output
