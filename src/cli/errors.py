"""Centralized error handling decorators for CLI commands."""

from collections.abc import Callable
from functools import wraps
from typing import TypeVar, cast

import click

from console_singleton import get_console
from exceptions import (
    CorruptedStateError,
    StateFileNotFoundError,
    WorkspaceBusyError,
    WorkspaceNotFoundError,
)

console = get_console()

F = TypeVar("F", bound=Callable)


def handle_state_errors(func: F) -> F:
    """Decorator to standardize state-related error handling.

    Catches StateFileNotFoundError, WorkspaceNotFoundError, and CorruptedStateError,
    prints them in red, and exits with code 1.

    Usage:
        @click.command()
        @handle_state_errors
        def my_command(ctx: click.Context):
            settings.validate_for_translation(input_epub)  # May raise state errors
            ...
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (
            StateFileNotFoundError,
            WorkspaceNotFoundError,
            CorruptedStateError,
            WorkspaceBusyError,
        ) as e:
            console.print(f"[red]{e}[/red]")
            raise click.exceptions.Exit(1)

    return cast(F, wrapper)


def handle_provider_errors(func: F) -> F:
    """Report a provider that cannot work at all, such as an unreachable Ollama,
    in red and exit with code 1, instead of a traceback."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        from translation.providers import ProviderFatalError

        try:
            return func(*args, **kwargs)
        except ProviderFatalError as e:
            console.print(f"[red]{e}[/red]")
            raise click.exceptions.Exit(1)

    return cast(F, wrapper)
