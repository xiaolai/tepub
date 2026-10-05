"""NLTK data and import handling for sentence splitting."""

from __future__ import annotations

import os

import pytest

from audiobook import preprocess


def test_a_failed_download_is_reported(monkeypatch) -> None:
    """nltk.download returns False offline; that used to surface later as a LookupError."""
    nltk = preprocess._nltk()

    def missing(_name):
        raise LookupError("not here")

    monkeypatch.setattr(nltk.data, "find", missing)
    monkeypatch.setattr(nltk, "download", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="punkt"):
        preprocess.ensure_punkt()


def test_the_import_guard_override_does_not_outlive_the_import(monkeypatch) -> None:
    """Setting NLTK_DISABLE_IMPORT_SECURITY for the whole process also disabled it
    for any program importing tepub as a library."""
    monkeypatch.delenv("NLTK_DISABLE_IMPORT_SECURITY", raising=False)
    preprocess._nltk()
    assert "NLTK_DISABLE_IMPORT_SECURITY" not in os.environ


def test_an_existing_setting_is_left_alone(monkeypatch) -> None:
    monkeypatch.setenv("NLTK_DISABLE_IMPORT_SECURITY", "0")
    preprocess._nltk()
    assert os.environ["NLTK_DISABLE_IMPORT_SECURITY"] == "0"
