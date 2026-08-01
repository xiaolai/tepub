"""Regression tests for prompt placeholders and Chinese target detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from state.models import ExtractMode, Segment, SegmentMetadata
from translation.polish import target_is_chinese
from translation.prompt_builder import build_prompt, configure_prompt


def _segment() -> Segment:
    return Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


@pytest.fixture(autouse=True)
def _reset_preamble():
    yield
    configure_prompt(None)


def test_custom_preamble_supports_language_instruction():
    """README and config.example.yaml document this placeholder; it raised KeyError."""
    configure_prompt("Custom preamble. {language_instruction}")

    out = build_prompt(_segment(), source_language="en", target_language="Simplified Chinese")

    assert "{language_instruction}" not in out
    assert "Simplified Chinese" in out


def test_custom_preamble_supports_all_documented_placeholders():
    configure_prompt(
        "{language_instruction} | {source_language} | {target_language} | {mode_instruction}"
    )

    out = build_prompt(_segment(), source_language="en", target_language="Simplified Chinese")

    assert "{" not in out.split("SOURCE:")[0]


@pytest.mark.parametrize(
    "language", ["Simplified Chinese", "zh", "zh-CN", "zh-TW", "zh_Hans", "中文"]
)
def test_chinese_targets_are_detected(language):
    """ISO codes were rejected, silently disabling Chinese typography formatting."""
    assert target_is_chinese(language) is True


@pytest.mark.parametrize("language", ["English", "en", "Japanese", "ja"])
def test_non_chinese_targets_are_not_detected(language):
    assert target_is_chinese(language) is False
