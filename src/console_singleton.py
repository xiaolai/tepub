"""Centralized console singleton for global quiet/verbose control."""

from __future__ import annotations

from rich.console import Console

_console: Console | None = None


def get_console() -> Console:
    """Get the shared Console instance.

    Returns:
        The global Console instance configured by configure_console().
        If not configured, returns a default Console instance.
    """
    global _console
    if _console is None:
        _console = Console()
    return _console


def configure_console(*, quiet: bool = False, verbose: bool = False) -> None:
    """Configure the global Console instance with quiet/verbose settings.

    Args:
        quiet: Suppress all console output (takes precedence over verbose)
        verbose: Enable verbose output (ignored if quiet=True)

    Note:
        This should be called once from main.py after parsing CLI flags.

        The shared instance is mutated rather than replaced. Command modules bind
        `console = get_console()` at import time, which happens before this runs,
        so replacing the singleton left every one of them holding the old object
        and --quiet had no effect anywhere.
    """
    # quiet takes precedence over verbose
    get_console().quiet = quiet
