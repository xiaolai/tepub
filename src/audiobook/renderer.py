from __future__ import annotations

import os
import random
import tempfile
from collections.abc import Sequence
from pathlib import Path

os.environ.setdefault("PYDUB_SIMPLE_AUDIOOP", "1")
from pydub import AudioSegment

from .preprocess import segment_to_text
from .tts import EdgeTTSEngine, OpenAITTSEngine, TTSEngine


class SegmentRenderer:
    def __init__(
        self,
        engine: TTSEngine,
        sentence_pause_range: tuple[float, float],
        epub_reader=None,
    ) -> None:
        self.engine = engine
        self.sentence_pause_range = sentence_pause_range
        self.epub_reader = epub_reader
        # Determine file extension based on engine type
        if isinstance(engine, OpenAITTSEngine):
            self.tts_extension = ".aac"
        else:
            self.tts_extension = ".mp3"

    def render_segment(
        self, segment_id: str, sentences: Sequence[str], output_dir: Path
    ) -> tuple[Path, float]:
        if not sentences:
            raise ValueError("No sentences to render")
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_seed = hash(segment_id) & 0xFFFFFFFF
        rng = random.Random(segment_seed)

        audio_parts: list[AudioSegment] = []
        with tempfile.TemporaryDirectory(prefix=f"{segment_id}-tts-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            for idx, sentence in enumerate(sentences):
                sentence_file = tmp_root / f"{idx:03d}{self.tts_extension}"
                self.engine.synthesize(sentence, sentence_file)
                part = AudioSegment.from_file(sentence_file)
                audio_parts.append(part)
                if idx < len(sentences) - 1:
                    pause_seconds = rng.uniform(*self.sentence_pause_range)
                    audio_parts.append(AudioSegment.silent(duration=int(pause_seconds * 1000)))

        combined = audio_parts[0]
        for part in audio_parts[1:]:
            combined += part

        output_path = output_dir / f"{segment_id}.m4a"
        combined.export(
            output_path,
            format="mp4",
            codec="aac",
            parameters=["-movflags", "+faststart", "-movie_timescale", "24000"],
        )
        return output_path, combined.duration_seconds
