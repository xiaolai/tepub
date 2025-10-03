from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetryConfig(BaseModel):
    max_attempts: int = Field(3, ge=1)
    backoff_seconds: float = Field(1.5, gt=0)
    jitter: float = Field(0.1, ge=0)


class RateLimitConfig(BaseModel):
    requests_per_minute: int | None = Field(None, gt=0)
    concurrency: int = Field(1, ge=1)


class ProviderConfig(BaseModel):
    name: str = Field(..., description="Provider identifier, e.g. openai or ollama")
    model: str = Field(..., description="Model name used for translation")
    base_url: str | None = None
    api_key: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _lowercase_name(cls, value: str) -> str:
        return value.lower()


class SkipRule(BaseModel):
    keyword: str
    reason: str = "auto-detected front/back matter"

    @field_validator("keyword")
    @classmethod
    def _normalize_keyword(cls, value: str) -> str:
        return value.strip().lower()


DEFAULT_ROOT_DIR = Path.cwd() / ".tepub"
_WORD_SPLIT_PATTERN = re.compile(r"[\s_-]+")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_WORKSPACE_HASH_LENGTH = 8


class AppSettings(BaseModel):
    work_root: Path = Field(default_factory=lambda: DEFAULT_ROOT_DIR)
    work_dir: Path = Field(default_factory=lambda: DEFAULT_ROOT_DIR)

    source_language: str = Field(default="auto")
    target_language: str = Field(default="Simplified Chinese")

    primary_provider: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(name="openai", model="gpt-4o")
    )
    fallback_provider: ProviderConfig | None = Field(
        default_factory=lambda: ProviderConfig(name="ollama", model="qwen2.5:14b-instruct")
    )

    retry: RetryConfig = Field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    skip_rules: list[SkipRule] = Field(
        default_factory=lambda: [
            SkipRule(keyword="cover"),
            SkipRule(keyword="praise"),
            SkipRule(keyword="also by"),
            SkipRule(keyword="copyright"),
            SkipRule(keyword="dedication"),
            SkipRule(keyword="acknowledgment"),
            SkipRule(keyword="acknowledgement"),
            SkipRule(keyword="the author"),
            SkipRule(keyword="further reading"),
            SkipRule(keyword="photograph"),
            SkipRule(keyword="credit"),
            SkipRule(keyword="glossary"),
            SkipRule(keyword="bibliography"),
            SkipRule(keyword="notes"),
            SkipRule(keyword="endnote"),
            SkipRule(keyword="endnotes"),
            SkipRule(keyword="index"),
            SkipRule(keyword="appendix"),
            SkipRule(keyword="appendices"),
            SkipRule(keyword="afterword"),
            SkipRule(keyword="reference"),
            SkipRule(keyword="references"),
        ]
    )

    # Back-matter cascade skipping configuration
    skip_after_back_matter: bool = True
    back_matter_triggers: list[str] = Field(
        default_factory=lambda: [
            "index",
            "notes",
            "endnotes",
            "bibliography",
            "references",
            "glossary",
        ]
    )
    back_matter_threshold: float = 0.7  # Only trigger in last 30% of TOC

    prompt_preamble: str | None = None
    output_mode: str = Field(default="bilingual")

    # Parallel processing settings
    translation_workers: int = Field(default=3, ge=1, description="Number of parallel workers for translation")
    audiobook_workers: int = Field(default=3, ge=1, description="Number of parallel workers for audiobook generation")

    # Per-book settings
    cover_image_path: Path | None = None
    audiobook_voice: str | None = None
    audiobook_opening_statement: str | None = None
    audiobook_closing_statement: str | None = None

    # TTS Provider settings
    audiobook_tts_provider: str = Field(default="edge", description="TTS provider: edge or openai")
    audiobook_tts_model: str | None = Field(default=None, description="TTS model (OpenAI: tts-1 or tts-1-hd)")
    audiobook_tts_speed: float = Field(default=1.0, ge=0.25, le=4.0, description="TTS speed for OpenAI (0.25-4.0)")

    # File inclusion lists (per-book config only)
    translation_files: list[str] | None = None
    audiobook_files: list[str] | None = None

    segments_file: Path = Field(default_factory=lambda: Path("segments.json"))
    state_file: Path = Field(default_factory=lambda: Path("state.json"))

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("output_mode")
    @classmethod
    def _normalise_output_mode(cls, value: str) -> str:
        if not value:
            return "bilingual"
        normalised = value.replace("-", "_").strip().lower()
        if normalised not in {"bilingual", "translated_only"}:
            raise ValueError("output_mode must be 'bilingual' or 'translated_only'")
        return normalised

    @field_validator("audiobook_tts_provider")
    @classmethod
    def _normalise_tts_provider(cls, value: str) -> str:
        if not value:
            return "edge"
        normalised = value.strip().lower()
        if normalised not in {"edge", "openai"}:
            raise ValueError("audiobook_tts_provider must be 'edge' or 'openai'")
        return normalised

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        work_root = self.work_root.expanduser()
        if not work_root.is_absolute():
            work_root = Path.cwd() / work_root
        object.__setattr__(self, "work_root", work_root)

        work_dir = self.work_dir.expanduser()
        if "work_dir" not in self.model_fields_set:
            work_dir = work_root
        elif not work_dir.is_absolute():
            work_dir = Path.cwd() / work_dir
        object.__setattr__(self, "work_dir", work_dir)

        if not self.segments_file.is_absolute():
            object.__setattr__(self, "segments_file", self.work_dir / self.segments_file)
        if not self.state_file.is_absolute():
            object.__setattr__(self, "state_file", self.work_dir / self.state_file)

    def ensure_directories(self) -> None:
        # Create work_dir (which creates work_root as parent if needed)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.segments_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> AppSettings:  # type: ignore[override]
        old_work_dir = self.work_dir
        copied: AppSettings = super().model_copy(update=update, deep=deep)
        new_work_dir = copied.work_dir

        overridden: set[str] = set(update.keys()) if update else set()

        if "work_dir" in overridden and "work_root" not in overridden:
            object.__setattr__(copied, "work_root", new_work_dir)

        if "work_dir" in overridden and new_work_dir != old_work_dir:
            copied._refresh_workdir_bound_paths(old_work_dir, overridden)

        return copied

    def _refresh_workdir_bound_paths(self, old_work_dir: Path, overridden: set[str]) -> None:
        for attr in ("segments_file", "state_file"):
            if attr in overridden:
                continue
            current = getattr(self, attr)
            try:
                relative = current.relative_to(old_work_dir)
            except ValueError:
                continue
            object.__setattr__(self, attr, self.work_dir / relative)

    def dump(self, path: Path) -> None:
        import json
        payload = json.loads(self.model_dump_json(indent=2))
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
