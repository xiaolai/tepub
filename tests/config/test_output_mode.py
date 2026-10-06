"""output_mode takes the same words as export --mode."""

from __future__ import annotations

import pytest

from config import AppSettings


@pytest.mark.parametrize(
    "value", ["translated", "translated-only", "translated_only", "Translated-Only"]
)
def test_every_spelling_of_translated(value: str) -> None:
    assert AppSettings(output_mode=value).output_mode == "translated_only"


def test_an_unknown_mode_is_refused_with_the_accepted_words() -> None:
    with pytest.raises(ValueError, match="'bilingual' or 'translated'"):
        AppSettings(output_mode="side-by-side")
