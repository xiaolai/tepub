"""NLTK data and import handling for sentence splitting."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from audiobook import preprocess


def test_a_failed_download_is_reported(monkeypatch) -> None:
    """nltk.download returns False offline; that used to surface later as a LookupError.

    NLTK 3.9 routes ``tokenizers/punkt`` to the punkt_tab data, so that is the
    only resource the tokenizer needs; a missing one must be reported by name.
    """
    nltk = preprocess._nltk()
    looked_up: list[str] = []

    def missing(name):
        looked_up.append(name)
        raise LookupError("not here")

    monkeypatch.setattr(nltk.data, "find", missing)
    monkeypatch.setattr(nltk, "download", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="punkt_tab"):
        preprocess.ensure_punkt()

    assert looked_up == ["tokenizers/punkt_tab"]


def _fresh_interpreter(code: str) -> subprocess.CompletedProcess:
    """Run ``code`` where nltk has not been imported yet.

    The in-process tests below run after another test imported nltk, so they
    exercise the cached-import path; a leak during the first import would not
    show there.
    """
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_import_guard_override_does_not_outlive_the_first_import() -> None:
    """Setting NLTK_DISABLE_IMPORT_SECURITY for the whole process also disabled it
    for any program importing tepub as a library."""
    result = _fresh_interpreter(
        "import os\n"
        "os.environ.pop('NLTK_DISABLE_IMPORT_SECURITY', None)\n"
        "from audiobook.preprocess import _nltk\n"
        "_nltk()\n"
        "print('leaked' if 'NLTK_DISABLE_IMPORT_SECURITY' in os.environ else 'clean')\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"


def test_an_existing_setting_is_left_alone_on_the_first_import() -> None:
    result = _fresh_interpreter(
        "import os\n"
        "os.environ['NLTK_DISABLE_IMPORT_SECURITY'] = '0'\n"
        "from audiobook.preprocess import _nltk\n"
        "_nltk()\n"
        "print(os.environ['NLTK_DISABLE_IMPORT_SECURITY'])\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_the_import_guard_override_does_not_outlive_a_cached_import(monkeypatch) -> None:
    monkeypatch.delenv("NLTK_DISABLE_IMPORT_SECURITY", raising=False)
    preprocess._nltk()
    assert "NLTK_DISABLE_IMPORT_SECURITY" not in os.environ
