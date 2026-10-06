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


def live_display_enabled(console: Console | None = None) -> bool:
    """Whether animated progress may be drawn: a terminal, and not --quiet.

    Progress bars drawn without a terminal filled log files with escape codes,
    and -q did not silence them.
    """
    console = console or get_console()
    return console.is_terminal and not console.quiet


class _NoLive:
    """Stands in for a rich Live where nothing should be drawn."""

    def __enter__(self) -> _NoLive:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def update(self, *args: object, **kwargs: object) -> None:
        pass


def live(renderable, *, console: Console | None = None, **kwargs):
    """A rich Live on a terminal; otherwise a stand-in that draws nothing."""
    from rich.live import Live

    console = console or get_console()
    if live_display_enabled(console):
        return Live(renderable, console=console, **kwargs)
    return _NoLive()


class PlainProgress:
    """Progress as an occasional line, for runs without a live display:
    "Translating: 125 of 600", every `every` units and at the end."""

    def __init__(self, label: str, total: int, *, every: int = 25, console: Console | None = None):
        self.label, self.total, self.every = label, total, every
        self.console = console or get_console()
        self.enabled = not live_display_enabled(self.console)

    def report(self, done: int) -> None:
        if self.enabled and done and (done % self.every == 0 or done == self.total):
            self.console.print(f"{self.label}: {done} of {self.total}")
