from pathlib import Path

from translation.prompt_builder import build_prompt, configure_prompt
from state.models import Segment, SegmentMetadata, ExtractMode


def _make_segment(text: str, mode: ExtractMode = ExtractMode.TEXT) -> Segment:
    return Segment(
        segment_id="seg-1",
        file_path=Path("Text/chapter1.xhtml"),
        xpath="/html/body/p[1]",
        extract_mode=mode,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


def test_build_prompt_includes_language_instructions() -> None:
    configure_prompt(None)  # Use DEFAULT_SYSTEM_PROMPT
    segment = _make_segment("Hello world")
    prompt = build_prompt(segment, source_language="en", target_language="zh-CN")
    assert "Translate from English" in prompt
    assert "Simplified Chinese" in prompt
    assert "SOURCE:\nHello world" in prompt


def test_build_prompt_handles_auto_detection() -> None:
    configure_prompt(None)  # Use DEFAULT_SYSTEM_PROMPT
    segment = _make_segment("<p>Hello</p>", ExtractMode.HTML)
    prompt = build_prompt(segment, source_language="auto", target_language="fr")
    assert "Detect the source language automatically" in prompt
    assert "Preserve HTML structure" in prompt
