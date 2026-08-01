"""Helpers for calling async APIs from synchronous audiobook code."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion from synchronous code.

    ``asyncio.run()`` raises ``RuntimeError`` when the calling thread already has
    a running event loop, which made TTS synthesis and voice listing fail for any
    caller embedding tepub in an async application. When a loop is already
    running here, the coroutine is executed on its own loop in a worker thread
    instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop in this thread — the common case.
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
