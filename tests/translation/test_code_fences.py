"""Replies wrapped in a code fence, or carrying tags the source never had.

On a whole book TranslateGemma returned three units as "```html ... ```": the
fence landed inside a list, invalid and visible, and a plain-text unit showed
its "<p>" to readers as literal text.
"""

from __future__ import annotations

from pathlib import Path

from state.models import ExtractMode, Segment, SegmentMetadata
from translation.controller import _translate_segment
from translation.markup import (
    markup_mismatch,
    protect,
    stray_tags,
    strip_code_fence,
    strip_model_artifacts,
)


def _segment(text: str, mode=ExtractMode.TEXT) -> Segment:
    return Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/x",
        extract_mode=mode,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )


class Replies:
    name = "fake"
    model = "fake-1"
    uses_markers = True
    follows_instructions = True

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.notes: list[str | None] = []

    def translate(self, segment, *, source_language, target_language):
        self.notes.append(segment.metadata.notes)
        return self.replies.pop(0)


def test_a_fence_around_the_reply_is_removed() -> None:
    assert strip_code_fence("```html\n<li>一</li>\n```") == "<li>一</li>"
    assert strip_code_fence("```\n一\n```") == "一"
    assert strip_code_fence("用 ``` 写代码") == "用 ``` 写代码"


def test_a_fenced_list_reply_is_used_without_its_fence() -> None:
    source = '<li><a href="#a">Introduction</a></li><li><a href="#b">Conclusion</a></li>'
    reply = '```html\n<li><a href="#a">引言</a></li><li><a href="#b">结论</a></li>\n```'
    result = _translate_segment(_segment(source, ExtractMode.HTML), Replies(reply), "en", "zh")
    assert result.error is None and "```" not in result.translation


def test_text_left_loose_in_a_list_is_a_markup_problem() -> None:
    source = "<li>One</li><li>Two</li>"
    assert "outside the list items" in (markup_mismatch(source, "html<li>一</li><li>二</li>") or "")
    assert markup_mismatch(source, "<li>一</li><li>二</li>") is None


def test_tags_added_to_plain_text_are_retried_then_refused() -> None:
    model = Replies("<p>聊天看似简单。</p>", "聊天看似简单。")
    result = _translate_segment(_segment("Chatting looks simple."), model, "en", "zh")
    assert result.error is None and result.translation == "聊天看似简单。"
    assert "added HTML tags <p>" in model.notes[1] and "plain text" in model.notes[1]


def test_escaped_tags_in_a_marker_reply_are_caught() -> None:
    source = 'See<a href="#n41">41</a> the report.'
    model = Replies("见&lt;p&gt;⟦1⟧41⟦/1⟧&lt;/p&gt;报告。", "见⟦1⟧41⟦/1⟧报告。")
    result = _translate_segment(_segment(source, ExtractMode.HTML), model, "en", "zh")
    assert result.error is None and result.translation == '见<a href="#n41">41</a>报告。'


def test_added_executable_attributes_are_caught() -> None:
    problem = markup_mismatch(
        '<img src="x.png"/>', '<img src="x.png" onerror="alert(1)"/>'
    )
    assert problem is not None and "onerror" in problem


def test_tags_the_source_itself_shows_are_allowed() -> None:
    assert stray_tags("Write <p> for a paragraph.", "用 <p> 写段落。") is None
    assert (
        stray_tags("A plain sentence.", "一个 <em>普通</em> 句子。")
        == "added HTML tags <em> to plain text"
    )


def test_a_fenced_copy_appended_to_the_translation_is_removed() -> None:
    """Measured: a title came back as the title, then a fenced escaped copy of it."""
    reply = (
        "第一章：为什么信任才是真正的价值\n\n"
        "```html\n&lt;h1&gt;第一章：为什么信任才是真正的价值&lt;/h1&gt;\n```"
    )
    assert (
        strip_model_artifacts(reply, markup=False)
        == "第一章：为什么信任才是真正的价值"
    )


def test_a_fenced_example_is_content_and_is_kept() -> None:
    """Only a duplicate escaped copy is an artifact; deleting from any opening
    fence onward also deleted legitimate code and the prose after it."""
    reply = "Example:\n```python\nprint(42)\n```"
    assert strip_model_artifacts(reply, markup=False) == reply


def test_a_fenced_example_the_source_itself_shows_is_kept() -> None:
    """A book about markup prints the example as text; a translation repeating
    it in a fence is reproducing the source, not echoing the reply.

    Escaping alone cannot tell this apart from the appended copy, so the source
    decides: its own visible tags or fence account for the tail.
    """
    source = "Hello world\n\n<p>Hello world</p>"
    reply = "Hello world\n\n```html\n&lt;p&gt;Hello world&lt;/p&gt;\n```"
    assert strip_model_artifacts(reply, markup=False, source=source) == reply


def test_a_translated_example_is_not_truncated() -> None:
    source = "Hello world\n\n<p>Hello world</p>"
    reply = "Hola mundo\n\n```html\n&lt;p&gt;Hola mundo&lt;/p&gt;\n```"
    result = _translate_segment(_segment(source), Replies(reply), "en", "es")
    assert result.error is None
    assert result.translation == reply


def test_a_unit_with_inline_code_keeps_the_html_route() -> None:
    """Marker protection collapsed whitespace inside <code>, so such units go
    the HTML route, where polish leaves code byte for byte."""
    source = "Use <code>a  b\nc</code> now."
    assert protect(source) is None

    result = _translate_segment(_segment(source, ExtractMode.HTML), Replies(source), "en", "zh")

    assert result.error is None and result.translation == source


def test_a_markup_fence_is_unwrapped_not_deleted() -> None:
    assert strip_model_artifacts("```html\n<p>一</p>\n```", markup=True) == "<p>一</p>"


def test_plain_prose_is_not_mistaken_for_markup() -> None:
    assert strip_model_artifacts("x < y and z > w", markup=False) == "x < y and z > w"


def test_a_tag_outside_a_fence_is_still_left_to_the_retry() -> None:
    """Silently deleting added HTML would hide a provider ignoring the prompt."""
    assert strip_model_artifacts("<p>一</p>", markup=False) == "<p>一</p>"


def test_the_fenced_tail_never_reaches_the_state() -> None:
    reply = "第一章\n\n```html\n&lt;h1&gt;第一章&lt;/h1&gt;\n```"
    result = _translate_segment(_segment("Original text"), Replies(reply), "en", "zh-CN")
    assert result.error is None
    assert "```" not in result.translation
