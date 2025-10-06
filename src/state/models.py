from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ExtractMode(str, Enum):
    TEXT = "text"
    HTML = "html"


class SegmentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    ERROR = "error"


class SegmentMetadata(BaseModel):
    element_type: str
    spine_index: int
    order_in_file: int
    notes: str | None = None


class Segment(BaseModel):
    segment_id: str = Field(..., description="Deterministic identifier for matching original nodes")
    file_path: Path = Field(..., description="Relative path to the XHTML file inside the EPUB")
    xpath: str = Field(..., description="Absolute XPath to the element in the document")
    extract_mode: ExtractMode
    source_content: str = Field(..., description="Content extracted pre-translation")
    metadata: SegmentMetadata
    skip_reason: str | None = Field(None, description="Reason for skipping (e.g., 'cover', 'index')")
    skip_source: str | None = Field(None, description="Source of skip decision (e.g., 'content', 'rule')")


class TranslationRecord(BaseModel):
    model_config = {"protected_namespaces": ()}

    segment_id: str
    translation: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    status: SegmentStatus = SegmentStatus.PENDING
    error_message: str | None = None


class SkippedDocument(BaseModel):
    file_path: Path
    reason: str
    source: str = "content"


class SegmentsDocument(BaseModel):
    epub_path: Path
    generated_at: str
    segments: list[Segment]
    skipped_documents: list[SkippedDocument] = Field(default_factory=list)

    # Book metadata (optional, extracted from EPUB)
    book_title: str | None = None
    book_author: str | None = None
    book_publisher: str | None = None
    book_year: str | None = None


class StateDocument(BaseModel):
    segments: dict[str, TranslationRecord] = Field(default_factory=dict)
    current_provider: str | None = None
    current_model: str | None = None
    version: int = 1
    source_language: str = "auto"
    target_language: str = "Simplified Chinese"
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None


class ResumeInfo(BaseModel):
    remaining_segments: list[str]
    completed_segments: list[str]
    skipped_segments: list[str]


def build_default_state(segments: list[Segment], provider: str, model: str) -> StateDocument:
    return StateDocument(
        segments={
            segment.segment_id: TranslationRecord(
                segment_id=segment.segment_id,
                status=SegmentStatus.PENDING,
            )
            for segment in segments
        },
        current_provider=provider,
        current_model=model,
    )
