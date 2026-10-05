from pathlib import Path

from state.models import ExtractMode, Segment, SegmentMetadata
from translation.prompt_builder import build_prompt, configure_prompt


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
    assert "Keep every tag" in prompt


def test_a_custom_prompt_without_the_mode_instruction_still_gets_it() -> None:
    """The per-book template omitted {mode_instruction}, so a whole book went
    to the model without being told to keep its markers."""
    segment = _make_segment("A claim⟦1⟧2⟦/1⟧.")
    configure_prompt("You translate {book} faithfully into {target_language}.")
    try:
        prompt = build_prompt(segment, "en", "zh")
    finally:
        configure_prompt(None)
    assert "Keep every marker exactly once" in prompt
    assert prompt.count("Keep every marker") == 1


def test_the_book_template_uses_the_mode_instruction(tmp_path: Path) -> None:
    import yaml

    from config.templates import create_book_config_template

    create_book_config_template(tmp_path, "book.epub", {"title": "A Book"})
    preamble = yaml.safe_load((tmp_path / "config.yaml").read_text())["prompt_preamble"]
    assert "{mode_instruction}" in preamble and "A Book" in preamble


def test_a_custom_prompt_naming_no_target_language_gets_the_language_instruction() -> None:
    configure_prompt("You are a careful literary translator.")
    try:
        prompt = build_prompt(_make_segment("Hello."), "en", "Simplified Chinese")
    finally:
        configure_prompt(None)
    assert "into Simplified Chinese" in prompt
    configure_prompt("Translate into {target_language} with care.")
    try:
        prompt = build_prompt(_make_segment("Hello."), "en", "Simplified Chinese")
    finally:
        configure_prompt(None)
    assert prompt.count("Simplified Chinese") == 1
