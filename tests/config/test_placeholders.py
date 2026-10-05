"""User-written templates fill named placeholders and leave other braces alone.

Both the custom translation prompt and the audiobook statements went through
str.format, so a prompt asking for JSON output, {"a": 1}, raised KeyError on
every segment, and a stray brace in a statement stopped assembly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.placeholders import fill_placeholders
from state.models import ExtractMode, Segment, SegmentMetadata
from translation import prompt_builder


def test_named_placeholders_are_filled() -> None:
    assert fill_placeholders("To {target}.", {"target": "French"}) == "To French."


def test_other_braces_are_left_as_written() -> None:
    template = 'Reply as {"text": "..."} in {target}; keep {unknown} and {0} and { spaced }.'
    assert fill_placeholders(template, {"target": "French"}) == (
        'Reply as {"text": "..."} in French; keep {unknown} and {0} and { spaced }.'
    )


def test_doubled_braces_still_mean_one_brace() -> None:
    # Templates written for str.format escaped braces by doubling them.
    assert fill_placeholders("{{target}} is {target}", {"target": "X"}) == "{target} is X"


@pytest.fixture
def custom_prompt():
    yield prompt_builder.configure_prompt
    prompt_builder.configure_prompt(None)


def test_a_custom_prompt_with_json_in_it_builds(custom_prompt) -> None:
    custom_prompt('Translate into {target_language}. Answer as {"translation": "..."}.')
    segment = Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello.",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    prompt = prompt_builder.build_prompt(segment, "en", "fr")
    assert 'Answer as {"translation": "..."}.' in prompt
    assert "{target_language}" not in prompt


def test_a_statement_with_a_stray_brace_renders(monkeypatch, tmp_path) -> None:
    from audiobook import statements
    from audiobook.models import AudioSessionConfig

    captured = {}

    def fake_generate(text, session, output_path):
        captured["text"] = text
        output_path.write_bytes(b"audio")  # the real generator writes the file
        return output_path

    monkeypatch.setattr(statements, "_generate_statement_audio", fake_generate)
    session = AudioSessionConfig(voice="en-US-JennyNeural", output_dir=tmp_path)
    statements._render_statement(
        "opening", "{book_name} by {author} {see notes}", session, tmp_path, "Moby-Dick", "Melville"
    )
    assert captured["text"] == "Moby-Dick by Melville {see notes}"
