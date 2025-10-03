"""Configuration module for tepub.

This module provides configuration management, workspace handling,
and template generation for the tepub EPUB translation tool.
"""

from __future__ import annotations

from config.loader import load_settings, load_settings_from_cli
from config.models import AppSettings, ProviderConfig, RateLimitConfig, RetryConfig, SkipRule
from config.templates import create_book_config_template
from config.workspace import build_workspace_name

# Import workspace module to attach methods to AppSettings
import config.workspace  # noqa: F401

__all__ = [
    # Models
    "AppSettings",
    "ProviderConfig",
    "SkipRule",
    "RetryConfig",
    "RateLimitConfig",
    # Loaders
    "load_settings",
    "load_settings_from_cli",
    # Workspace
    "build_workspace_name",
    # Templates
    "create_book_config_template",
]
