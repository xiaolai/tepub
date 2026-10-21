"""Ctrl-C ends the run at once, with exit code 130, and keeps finished work.

The run used to save its state and then hang for as long as any worker thread
was still inside a network call or a retry sleep, because Python waits for
those threads at exit. The audiobook command also exited 0 when interrupted.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from state.models import SegmentStatus
from state.store import load_state

DRIVER = Path(__file__).with_name("interrupt_driver.py")
READY_TIMEOUT = 20


def _read_ready(proc: subprocess.Popen[str], errors: Path) -> str:
    """The child's next line, waiting at most READY_TIMEOUT seconds.

    select says only that a byte is waiting. A child that wrote half a line
    and stalled then left readline blocking with no deadline at all, so the
    timeout after it was never reached.
    """
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + READY_TIMEOUT
    received = b""
    while b"\n" not in received:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([fd], [], [], max(remaining, 0))
        if not readable:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        received += chunk
    if b"\n" in received:
        return received.split(b"\n", 1)[0].decode(
            proc.stdout.encoding or "utf-8", errors="replace"
        ).strip()
    proc.kill()
    proc.wait()
    stacks = errors.read_text(encoding="utf-8", errors="replace")
    raise AssertionError(
        f"no READY line {READY_TIMEOUT} s after start; got {received!r}; stderr:\n"
        f"{stacks[-6000:]}"
    )


def test_ctrl_c_exits_promptly_with_130_and_saves(tmp_path: Path) -> None:
    # stderr goes to a file: an unread pipe that fills blocks the child.
    errors = tmp_path / "stderr.txt"
    proc = subprocess.Popen(
        [sys.executable, str(DRIVER), str(tmp_path), "guarded"],
        stdout=subprocess.PIPE,
        stderr=errors.open("w"),
        text=True,
    )
    try:
        assert _read_ready(proc, errors) == "READY"
        sent = time.monotonic()
        proc.send_signal(signal.SIGINT)
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            # Ask the driver for every thread's stack, so a hang says where.
            proc.send_signal(signal.SIGUSR1)
            time.sleep(1)
            proc.kill()
            proc.wait()
            stacks = errors.read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"no exit 20 s after Ctrl-C; threads:\n{stacks[-6000:]}") from None
        elapsed = time.monotonic() - sent
    finally:
        if proc.poll() is None:
            proc.kill()

    assert code == 130
    assert elapsed < 2.0, f"exit took {elapsed:.1f} s"
    state = load_state(tmp_path / "state.json")
    done = [r for r in state.segments.values() if r.status == SegmentStatus.COMPLETED]
    assert len(done) == 2  # the two calls that finished before the blocking third
