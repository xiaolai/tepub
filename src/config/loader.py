from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from config.models import AppSettings
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

# The shape of an environment variable name. A .env entry of this shape, such as
# OPENAI_API_KEY, is exported to the process; anything else is a setting.
_ENV_VAR_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def _apply_env_file(path: Path, payload: dict[str, Any]) -> None:
    """Read a .env file: export variables, merge settings.

    API keys used to be merged into the settings payload, where they are not
    fields and were dropped, while the providers read them from the process
    environment. A variable already exported in the shell is left as it is.
    """
    for key, value in _parse_env_file(path).items():
        if _ENV_VAR_NAME.fullmatch(key):
            os.environ.setdefault(key, value)
        else:
            payload[key] = value


def _parse_yaml_file(path: Path) -> dict[str, Any]:
    """Parse YAML file into dictionary."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    if yaml:
        loaded = yaml.safe_load(text)
        return loaded or {}
    # No usable fallback: the previous one handled only top-level "key: value"
    # pairs, so it silently misparsed the nested providers block, lists, and the
    # `prompt_preamble: |` block scalar that the generated per-book config always
    # contains — producing a config that looked valid and was not. PyYAML is now
    # a declared dependency, so reaching here means a broken environment.
    raise RuntimeError(
        f"Cannot parse {path}: PyYAML is not installed. "
        "Reinstall tepub to repair the environment: pip install -e ."
    )


def _prepare_provider_credentials(settings: AppSettings) -> AppSettings:
    """Inject API keys and base URLs from environment variables."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and settings.primary_provider.name == "openai":
        settings.primary_provider.api_key = openai_key
    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if ollama_url:
        if settings.primary_provider.name == "ollama":
            settings.primary_provider.base_url = ollama_url
        if settings.fallback_provider and settings.fallback_provider.name == "ollama":
            settings.fallback_provider.base_url = ollama_url
    return settings


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
    payload: dict[str, Any] = {}

    # Load global config from ~/.tepub/config.yaml first
    global_config = Path.home() / ".tepub" / "config.yaml"
    if global_config.exists():
        global_payload = _parse_yaml_file(global_config)
        if isinstance(global_payload, dict):
            payload.update(global_payload)

    default_env = Path(".env")
    if default_env.exists():
        _apply_env_file(default_env, payload)

    # Load project config.yaml (can override global)
    yaml_path = Path("config.yaml")
    if yaml_path.exists():
        yaml_payload = _parse_yaml_file(yaml_path)
        if isinstance(yaml_payload, dict):
            payload.update(yaml_payload)

    env_root = os.getenv("TEPUB_WORK_ROOT")
    if env_root:
        root_path = Path(env_root).expanduser()
        payload["work_root"] = root_path
        payload.setdefault("work_dir", root_path)

    if config_path:
        config_path = config_path.expanduser()
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            payload.update(_parse_yaml_file(config_path))
        else:
            _apply_env_file(config_path, payload)

    # Convert known keys to structured data if present
    if "work_dir" in payload:
        payload["work_dir"] = Path(payload["work_dir"]).expanduser()
        if "work_root" not in payload:
            payload["work_root"] = payload["work_dir"]
    if "work_root" in payload:
        payload["work_root"] = Path(payload["work_root"]).expanduser()
    if "source_language" in payload:
        payload["source_language"] = str(payload["source_language"]).strip()
    if "target_language" in payload:
        payload["target_language"] = str(payload["target_language"]).strip()
    if "skip_rules" in payload and isinstance(payload["skip_rules"], list):
        normalized_rules: list[dict[str, Any]] = []
        for item in payload["skip_rules"]:
            if isinstance(item, str):
                normalized_rules.append({"keyword": item})
            elif isinstance(item, dict):
                normalized_rules.append(item)
        payload["skip_rules"] = normalized_rules
    if "prompt_preamble" in payload and payload["prompt_preamble"] is not None:
        payload["prompt_preamble"] = str(payload["prompt_preamble"]).strip()
    if "output_mode" in payload:
        payload["output_mode"] = str(payload["output_mode"]).replace("-", "_").strip().lower()

    settings = AppSettings(**payload)
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
    return load_settings(Path(config_file).expanduser()) if config_file else load_settings()
