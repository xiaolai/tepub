from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from config.provenance import ConfigLayer


class ProviderConfig(BaseModel):
    # An instance passed into a settings update is checked again: a copy made
    # with model_copy(update=...) skips validation, so max_tokens=0 got through.
    model_config = ConfigDict(revalidate_instances="always")

    name: str = Field(..., description="Provider identifier, e.g. openai or ollama")
    model: str = Field(..., description="Model name used for translation")
    base_url: str | None = None
    api_key: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    max_tokens: int = Field(
        default=8192,
        gt=0,
        description="Maximum tokens in a single translation response. A long segment "
        "that exceeds this is reported as an error rather than silently truncated.",
    )
    think: bool | None = Field(
        default=None,
        description="Ollama only. false turns off a reasoning model's thinking, which "
        "otherwise costs minutes per paragraph; unset leaves the model's default.",
    )

    @field_validator("name")
    @classmethod
    def _lowercase_name(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def _think_is_ollama_only(self) -> ProviderConfig:
        if self.think is not None and self.name != "ollama":
            raise ValueError(
                f"think is an Ollama option; provider {self.name!r} has no such setting"
            )
        return self


class SkipRule(BaseModel):
    keyword: str
    reason: str = "auto-detected front/back matter"

    @field_validator("keyword")
    @classmethod
    def _normalize_keyword(cls, value: str) -> str:
        keyword = value.strip().lower()
        # An empty keyword is in every title, so it matched first and hid the
        # rules after it.
        if not keyword:
            raise ValueError("a skip rule keyword cannot be empty")
        return keyword


def _default_root_dir() -> Path:
    """Resolve the default workspace root at call time.

    This was computed once at import, so any settings constructed after a working
    directory change still pointed at the original directory.
    """
    return Path.cwd() / ".tepub"


_WORD_SPLIT_PATTERN = re.compile(r"[\s_-]+")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_WORKSPACE_HASH_LENGTH = 8


class AppSettings(BaseModel):
    work_root: Path = Field(default_factory=_default_root_dir)
    work_dir: Path = Field(default_factory=_default_root_dir)

    source_language: str = Field(default="auto")
    target_language: str = Field(default="Simplified Chinese")

    primary_provider: ProviderConfig = Field(
        # Local by default: a book is translated on the user's own machine, at no
        # cost per word, with nothing sent to a third party.
        default_factory=lambda: ProviderConfig(name="ollama", model="translategemma:12b")
    )

    skip_rules: list[SkipRule] = Field(
        default_factory=lambda: [
            SkipRule(keyword="cover"),
            SkipRule(keyword="praise"),
            SkipRule(keyword="also by"),
            SkipRule(keyword="copyright"),
            SkipRule(keyword="dedication"),
            SkipRule(keyword="acknowledgment"),
            SkipRule(keyword="acknowledgement"),
            # Plurals are listed, not inferred: "indexes" in a title is rarely
            # an index ("Naming Loop Indexes").
            SkipRule(keyword="acknowledgments"),
            SkipRule(keyword="acknowledgements"),
            SkipRule(keyword="credits"),
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
    back_matter_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        # Interpreted as a fraction of the TOC. It previously accepted negatives,
        # values above one, NaN and infinity, each of which silently disabled or
        # inverted back-matter detection instead of being rejected.
        description="Only trigger back-matter skipping in the last (1 - threshold) of the TOC",
    )

    prompt_preamble: str | None = None
    output_mode: str = Field(default="bilingual")

    # Parallel processing settings
    translation_workers: int = Field(
        default=3, ge=1, description="Number of parallel workers for translation"
    )
    audiobook_workers: int = Field(
        default=3, ge=1, description="Number of parallel workers for audiobook generation"
    )

    # Per-book settings
    cover_image_path: Path | None = None
    audiobook_voice: str | None = None
    audiobook_opening_statement: str | None = None
    audiobook_closing_statement: str | None = None

    # TTS Provider settings
    audiobook_tts_provider: str = Field(default="edge", description="TTS provider: edge or openai")
    audiobook_tts_model: str | None = Field(
        default=None, description="TTS model (OpenAI: tts-1 or tts-1-hd)"
    )
    # None means "not configured": a default of 1.0 was indistinguishable from an
    # explicit setting, so it overrode the speed a resumed session was started with.
    audiobook_tts_speed: float | None = Field(
        default=None, ge=0.25, le=4.0, description="TTS speed for OpenAI (0.25-4.0)"
    )

    # File inclusion lists (per-book config only)
    translation_files: list[str] | None = None
    audiobook_files: list[str] | None = None

    segments_file: Path = Field(default_factory=lambda: Path("segments.json"))
    state_file: Path = Field(default_factory=lambda: Path("state.json"))

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # The config files these settings were loaded from, recorded by the loader.
    _config_layers: tuple[ConfigLayer, ...] = PrivateAttr(default=())

    @property
    def config_layers(self) -> tuple[ConfigLayer, ...]:
        """Each config file read for these settings, in the order applied."""
        return self._config_layers

    @config_layers.setter
    def config_layers(self, layers: tuple[ConfigLayer, ...]) -> None:
        self._config_layers = layers

    @field_validator("work_root", "work_dir", "segments_file", "state_file")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        # A `~name` naming no user raised RuntimeError, a traceback rather
        # than a validation error naming the setting.
        try:
            return value.expanduser()
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("skip_rules", mode="before")
    @classmethod
    def _accept_bare_keywords(cls, value: Any) -> Any:
        # "- index" as well as "- keyword: index", in every config that is read:
        # only the main loader accepted the short form, so a book's config.yaml
        # using it failed.
        if isinstance(value, list):
            return [{"keyword": item} if isinstance(item, str) else item for item in value]
        return value

    @field_validator("back_matter_triggers")
    @classmethod
    def _normalize_triggers(cls, value: list[str]) -> list[str]:
        # Titles are lowercased before matching, so "Index" never matched.
        triggers = [trigger.strip().lower() for trigger in value]
        if not all(triggers):
            raise ValueError("a back_matter_triggers entry cannot be empty")
        return triggers

    @field_validator("source_language", "target_language", "prompt_preamble", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("back_matter_threshold")
    @classmethod
    def _reject_non_finite_threshold(cls, value: float) -> float:
        # ge/le comparisons are all False for NaN, so it slips past the bounds.
        if not math.isfinite(value):
            raise ValueError("back_matter_threshold must be a finite number between 0 and 1")
        return value

    @field_validator("output_mode")
    @classmethod
    def _normalise_output_mode(cls, value: str) -> str:
        if not value:
            return "bilingual"
        normalised = value.replace("-", "_").strip().lower()
        # The same words export --mode takes: "translated" was refused here.
        if normalised == "translated":
            normalised = "translated_only"
        if normalised not in {"bilingual", "translated_only"}:
            raise ValueError(
                "output_mode must be 'bilingual' or 'translated' (also 'translated-only')"
            )
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
        # Paths given are expanded by _expand_user; defaults hold no `~`.
        work_root = self.work_root
        if not work_root.is_absolute():
            work_root = Path.cwd() / work_root
        object.__setattr__(self, "work_root", work_root)

        work_dir = self.work_dir
        if "work_dir" not in self.model_fields_set:
            work_dir = work_root
        elif not work_dir.is_absolute():
            work_dir = Path.cwd() / work_dir
        object.__setattr__(self, "work_dir", work_dir)

        for attr in ("segments_file", "state_file"):
            path = getattr(self, attr)
            if not path.is_absolute():
                path = self.work_dir / path
            object.__setattr__(self, attr, path)

    def ensure_directories(self) -> None:
        # Create work_dir (which creates work_root as parent if needed)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.segments_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def model_copy(  # type: ignore[override]
        self, *, update: dict[str, Any] | None = None, deep: bool = False
    ) -> AppSettings:
        if not update:
            return super().model_copy(deep=deep)

        old_work_dir = self.work_dir
        # Pydantic's model_copy assigns updates without running validators or
        # model_post_init, so invalid worker counts, unknown output modes, strings
        # in Path fields and dicts in ProviderConfig fields were accepted here and
        # only crashed later in whichever consumer used them. Rebuilding through
        # model_validate applies the same checks a fresh construction would.
        # Paths that were derived rather than set are left out, so they are
        # derived again from the updated values: carried over, a copy with a
        # new work_root kept the old work_dir and artifact paths.
        derived = {"work_dir", "segments_file", "state_file"} - self.model_fields_set
        merged = {**self.model_dump(exclude=derived), **update}
        copied: AppSettings = type(self).model_validate(merged)
        # Rebuilt rather than copied, so where the settings came from is
        # carried across by hand.
        copied.config_layers = self.config_layers
        new_work_dir = copied.work_dir

        overridden: set[str] = set(update.keys()) if update else set()

        if "work_dir" in overridden and "work_root" not in overridden:
            object.__setattr__(copied, "work_root", new_work_dir)

        if "work_dir" in overridden and new_work_dir != old_work_dir:
            # Paths never set were derived from the new work_dir already.
            settled = overridden | derived
            copied._refresh_workdir_bound_paths(old_work_dir, settled)

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

    # The workspace operations live in config.workspace, which imports this module.
    def derive_book_workspace(self, input_epub: Path) -> Path:
        from config.workspace import derive_book_workspace

        return derive_book_workspace(self, input_epub)

    def with_book_workspace(self, input_epub: Path) -> AppSettings:
        from config.workspace import with_book_workspace

        return with_book_workspace(self, input_epub)

    def with_override_root(self, base_path: Path, input_epub: Path) -> AppSettings:
        from config.workspace import with_override_root

        return with_override_root(self, base_path, input_epub)

    def validate_for_export(self, input_epub: Path) -> None:
        from config.workspace import validate_for_export

        validate_for_export(self, input_epub)

    def validate_for_translation(self, input_epub: Path) -> None:
        from config.workspace import validate_for_translation

        validate_for_translation(self, input_epub)

    def dump(self, path: Path) -> None:
        import json
        payload = json.loads(self.model_dump_json(indent=2))
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
