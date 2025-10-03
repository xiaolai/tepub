from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class AudioSegmentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


class AudioSegmentState(BaseModel):
    segment_id: str
    status: AudioSegmentStatus = AudioSegmentStatus.PENDING
    attempts: int = 0
    audio_path: Path | None = None
    duration_seconds: float | None = None
    last_error: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AudioSessionConfig(BaseModel):
    voice: str
    language: str | None = None
    output_dir: Path
    sentence_pause_range: tuple[float, float] = (1.0, 2.0)
    segment_pause_range: tuple[float, float] = (2.0, 4.0)
    cover_path: Path | None = None
    # TTS provider settings
    tts_provider: str = "edge"
    tts_model: str | None = None  # For OpenAI: tts-1 or tts-1-hd
    tts_speed: float = 1.0  # For OpenAI: 0.25-4.0


class AudioStateDocument(BaseModel):
    session: AudioSessionConfig
    segments: dict[str, AudioSegmentState]
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
