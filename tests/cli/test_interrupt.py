"""Ctrl-C ends the run at once, with exit code 130, and keeps finished work.

The run used to save its state and then hang for as long as any worker thread
was still inside a network call or a retry sleep, because Python waits for
those threads at exit. The audiobook command also exited 0 when interrupted.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

from state.models import SegmentStatus
from state.store import load_state

DRIVER = Path(__file__).with_name("interrupt_driver.py")


def test_ctrl_c_exits_promptly_with_130_and_saves(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, str(DRIVER), str(tmp_path), "guarded"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "READY"
        sent = time.monotonic()
        proc.send_signal(signal.SIGINT)
        code = proc.wait(timeout=20)
        elapsed = time.monotonic() - sent
    finally:
        if proc.poll() is None:
            proc.kill()

    assert code == 130
    assert elapsed < 2.0, f"exit took {elapsed:.1f} s"
    state = load_state(tmp_path / "state.json")
    done = [r for r in state.segments.values() if r.status == SegmentStatus.COMPLETED]
    assert len(done) == 2  # the two calls that finished before the blocking third
