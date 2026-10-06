from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.models import AppSettings
from config.provenance import ConfigLayer
from exceptions import TepubError
from logging_utils.logger import get_logger

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse .env file into dictionary."""
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip("'").strip('"')
    return data


logger = get_logger(__name__)

# The variables tepub itself reads from the environment. Only these may be set
# from a .env file: it is read from whatever directory tepub runs in, and
# exporting every entry let such a file route API traffic through a proxy
# (HTTPS_PROXY) or inject a library into ffmpeg (DYLD_INSERT_LIBRARIES).
DOTENV_EXPORTABLE = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "DEEPL_API_KEY",
        "GEMINI_API_KEY",
        "GROK_API_KEY",
        "OLLAMA_BASE_URL",
        "OPENAI_API_KEY",
    }
)

# The shape of an environment variable name; settings are lowercase.
_ENV_VAR_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def _apply_env_file(path: Path) -> dict[str, Any]:
    """Read a .env file: export provider variables, return the settings it sets.

    API keys used to be merged into the settings payload, where they are not
    fields and were dropped, while the providers read them from the process
    environment. A variable already exported in the shell is left as it is.
    Any other variable-shaped entry is ignored with a warning.
    """
    payload: dict[str, Any] = {}
    for key, value in _parse_env_file(path).items():
        if key in DOTENV_EXPORTABLE:
            os.environ.setdefault(key, value)
        elif _ENV_VAR_NAME.fullmatch(key):
            logger.warning(
                "Ignoring %s in %s: a .env file may only set %s",
                key,
                path,
                ", ".join(sorted(DOTENV_EXPORTABLE)),
            )
        else:
            payload[key] = value
    return payload


class ConfigFileError(TepubError):
    """A config file that holds something other than settings."""


def _parse_yaml_file(path: Path) -> dict[str, Any]:
    """Parse YAML file into dictionary.

    An empty file, or one holding only comments, sets nothing. Any other
    document must be a mapping: `loaded or {}` turned `false`, `0`, `""`, `[]`
    and an explicit `null` into no settings at all, so such a file was accepted
    without a word. Telling the two apart needs the document itself: an empty
    stream composes to no node, `null` or `~` to a null scalar.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml:
        if yaml.compose(text, Loader=yaml.SafeLoader) is None:
            return {}
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigFileError(
                f"{path} must hold settings as `name: value` lines, "
                f"not a {type(loaded).__name__}."
            )
        # A key such as `1:` is not a setting name; it reached sorted() beside
        # the text keys and AppSettings(**payload) as a TypeError traceback.
        odd = [key for key in loaded if not isinstance(key, str)]
        if odd:
            raise ConfigFileError(
                f"{path}: setting names must be text, not "
                + ", ".join(repr(key) for key in odd)
                + "."
            )
        return loaded
    # No usable fallback: the previous one handled only top-level "key: value"
    # pairs, so it silently misparsed the nested providers block, lists, and the
    # `prompt_preamble: |` block scalar that the generated per-book config always
    # contains — producing a config that looked valid and was not. PyYAML is now
    # a declared dependency, so reaching here means a broken environment.
    raise RuntimeError(
        f"Cannot parse {path}: PyYAML is not installed. "
        "Reinstall tepub to repair the environment: pip install -e ."
    )


def _prepare_provider_credentials(
    settings: AppSettings, keep: frozenset[str] = frozenset()
) -> AppSettings:
    """Inject API keys and base URLs from environment variables.

    The environment overrides every config file load_settings reads; a book's
    own config.yaml overrides the environment in turn, so the provider fields
    it sets are passed as `keep` and left as the book wrote them.
    """
    provider = settings.primary_provider
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and provider.name == "openai" and "api_key" not in keep:
        provider.api_key = openai_key
    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if ollama_url and provider.name == "ollama" and "base_url" not in keep:
        provider.base_url = ollama_url
    return settings


def expand_user(path: Path) -> Path:
    """`path` with `~` expanded; a `~name` naming no user is a ConfigFileError.

    Path.expanduser raises RuntimeError for an unknown user, which escaped the
    command line's error handling as a traceback.
    """
    try:
        return path.expanduser()
    except RuntimeError as exc:
        raise ConfigFileError(f"Cannot use the path {path}: {exc}") from exc


def _expand_path(value: Any) -> Any:
    """`~` expanded in a path given as text; anything else unchanged."""
    return expand_user(Path(value)) if isinstance(value, (str, Path)) else value


def _read_layer(
    label: str, path: Path, parse: Callable[[Path], dict[str, Any]], *, always: bool = False
) -> ConfigLayer:
    """A config file as the loader read it: whether it is there, what it sets.

    `always` parses a file that is not there, so a missing --config fails as
    it always has rather than passing as an absent file.
    """
    found = path.exists()
    payload = parse(path) if found or always else {}
    return ConfigLayer(label, path, found, payload)


def load_settings(config_path: Path | None = None) -> AppSettings:
    """Load configuration from multiple sources with proper precedence.

    Loading order (later overrides earlier):
    1. Global config: ~/.tepub/config.yaml
    2. Environment file: .env
    3. Project config: config.yaml
    4. Environment variable: TEPUB_WORK_ROOT
    5. Explicit config file: config_path parameter

    Args:
        config_path: Optional path to explicit config file (YAML or .env)

    Returns:
        AppSettings instance with merged configuration
    """
    layers = [
        _read_layer("global", Path.home() / ".tepub" / "config.yaml", _parse_yaml_file),
        _read_layer(".env", Path(".env"), _apply_env_file),
        _read_layer("./config.yaml", Path("config.yaml"), _parse_yaml_file),
    ]
    payload: dict[str, Any] = {}
    for layer in layers:
        payload.update(layer.payload)

    env_root = os.getenv("TEPUB_WORK_ROOT")
    if env_root:
        root_path = expand_user(Path(env_root))
        payload["work_root"] = root_path
        payload.setdefault("work_dir", root_path)

    if config_path:
        config_path = expand_user(config_path)
        is_yaml = config_path.suffix.lower() in {".yaml", ".yml"}
        layer = _read_layer(
            "--config", config_path, _parse_yaml_file if is_yaml else _apply_env_file, always=True
        )
        layers.append(layer)
        payload.update(layer.payload)

    # Convert known keys to structured data if present. Only text is a path:
    # Path(None) for `work_dir: null` ended in a TypeError traceback; any other
    # value is left for AppSettings to reject with a validation error.
    if "work_dir" in payload:
        payload["work_dir"] = _expand_path(payload["work_dir"])
        if "work_root" not in payload:
            payload["work_root"] = payload["work_dir"]
    if "work_root" in payload:
        payload["work_root"] = _expand_path(payload["work_root"])
    # Skip rules in short form, text trimming and output_mode spellings are
    # normalised by AppSettings validators, so `config validate` (which builds
    # AppSettings from a file directly) accepts exactly what is loaded here.

    # Unknown keys used to be dropped without a word, so a misspelt setting, or
    # one tepub no longer has, looked accepted.
    unknown = sorted(set(payload) - set(AppSettings.model_fields))
    if unknown:
        logger.warning("Ignoring settings tepub does not use: %s", ", ".join(unknown))
    settings = AppSettings(**payload)
    # What each file set, kept for `tepub config show` to name each setting's
    # origin from what was read here rather than by reading the files again.
    settings.config_layers = tuple(layers)
    configured = _prepare_provider_credentials(settings)
    try:
        from translation.prompt_builder import configure_prompt

        configure_prompt(configured.prompt_preamble)
    except ImportError:
        # Narrowed from `except Exception`, which also swallowed genuine errors
        # raised while configuring the prompt and left translation running on a
        # stale prompt with no indication.
        logger.warning("Prompt builder unavailable; using the default prompt.")
        pass
    return configured


def load_settings_from_cli(config_file: str | None) -> AppSettings:
    """CLI entry point for loading settings."""
    return load_settings(expand_user(Path(config_file))) if config_file else load_settings()
