from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

_RICH_HANDLER: RichHandler | None = None


def configure_logging(level: int = logging.INFO) -> None:
    global _RICH_HANDLER
    if _RICH_HANDLER is None:
        console = Console(force_terminal=True)
        handler = RichHandler(
            console=console, show_time=False, show_path=False, rich_tracebacks=True
        )
        logging.basicConfig(level=level, handlers=[handler], format="%(message)s")
        _RICH_HANDLER = handler
        # Silence httpx HTTP request logs (used by OpenAI SDK)
        logging.getLogger("httpx").setLevel(logging.WARNING)
    else:
        logging.getLogger().setLevel(level)
        _RICH_HANDLER.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    # Only bootstrap if logging was never configured. Calling configure_logging()
    # unconditionally passed the default INFO level, so any get_logger() after
    # `--verbose` reset the root logger and handler back to INFO and silently
    # turned debug logging off.
    if _RICH_HANDLER is None:
        configure_logging()
    return logging.getLogger(name)
