"""NLTK data and import handling for sentence splitting."""

from __future__ import annotations


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

