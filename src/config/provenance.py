"""Where each setting in effect came from, for `tepub config show`.

config.loader records each file it reads as a ConfigLayer on the settings it
returns, and config.workspace adds the book's own config.yaml; this module
only reads those records. The layers, in order: the files load_settings reads,
then the environment's OLLAMA_BASE_URL, then the book's own config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config.models import AppSettings

# Keys shown by `config show`, with how to find each in a config file.
SHOWN = (
    ("primary_provider.name", ("primary_provider", "name")),
    ("primary_provider.model", ("primary_provider", "model")),
    ("primary_provider.base_url", ("primary_provider", "base_url")),
    ("primary_provider.think", ("primary_provider", "think")),
    ("primary_provider.max_tokens", ("primary_provider", "max_tokens")),
    ("source_language", ("source_language",)),
    ("target_language", ("target_language",)),
    ("output_mode", ("output_mode",)),
    ("translation_workers", ("translation_workers",)),
    ("skip_after_back_matter", ("skip_after_back_matter",)),
    ("prompt_preamble", ("prompt_preamble",)),
)

BOOK = "book"
OLLAMA_ENV = "OLLAMA_BASE_URL"
_BASE_URL = ("primary_provider", "base_url")
_MISSING = object()


@dataclass(frozen=True)
class ConfigLayer:
    """A config file tepub reads: whether it is there, and the settings it set."""

    label: str
    path: Path
    found: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class ShownSetting:
    """A setting in effect and the layer it came from."""

    name: str
    value: Any
    origin: str


def _lookup(payload: dict, path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _file_origin(path: tuple[str, ...], layers: tuple[ConfigLayer, ...]) -> str:
    """Which file set a setting: the last one holding it, or default.

    Files replace a whole top-level value, so a primary_provider in a later file
    drops every provider field an earlier one set; those fall back to default.
    """
    origin = "default"
    for layer in layers:
        if _lookup(layer.payload, path) is not _MISSING:
            origin = layer.label
        elif len(path) > 1 and path[0] in layer.payload:
            origin = "default"
    return origin


def _origin(path: tuple[str, ...], settings: AppSettings, ollama_url: str | None) -> str:
    """The layer a setting came from.

    OLLAMA_BASE_URL overrides every file the loader reads and sets an Ollama
    provider's address; only an address in the book's own config overrides it.
    """
    layers = settings.config_layers
    if path == _BASE_URL and ollama_url and settings.primary_provider.name == "ollama":
        book = next((layer.payload for layer in layers if layer.label == BOOK), {})
        if _lookup(book, path) is _MISSING:
            return OLLAMA_ENV
    return _file_origin(path, layers)


def shown_settings(settings: AppSettings, ollama_url: str | None) -> list[ShownSetting]:
    """The settings `config show` lists, each with its value and origin."""
    shown = []
    for name, path in SHOWN:
        value: Any = settings
        for key in path:
            value = getattr(value, key)
        shown.append(ShownSetting(name, value, _origin(path, settings, ollama_url)))
    return shown
