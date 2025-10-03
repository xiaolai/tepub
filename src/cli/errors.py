"""Centralized error handling decorators for CLI commands."""

from collections.abc import Callable
from functools import wraps
from typing import TypeVar, cast

import click

from console_singleton import get_console
from exceptions import (
    CorruptedStateError,
    StateFileNotFoundError,
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
        except (StateFileNotFoundError, WorkspaceNotFoundError, CorruptedStateError) as e:
            console.print(f"[red]{e}[/red]")
            raise click.exceptions.Exit(1)

    return cast(F, wrapper)
