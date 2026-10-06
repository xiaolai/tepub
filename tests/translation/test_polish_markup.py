"""Typography formatting touches the text of a translation, never its markup.

polish_text runs on every reply in translate, where it sees either an HTML
fragment or text with tepub's numbered markers (translation.markup), and on
every stored translation in `tepub format`, where it sees HTML. It used to
format the whole string, attributes included: href="chapter.xhtml#中文2024"
became "#中文 2024" and the link broke.

Across a tag or marker boundary the behaviour is kept as it was: no space is
added between Chinese and a tag's Latin or digit content ("中文<em>English</em>",
"中文<sup>1</sup>"), because the tag may be a note reference that must sit
against its word, and whitespace rules still act across it (the space after a
full-width full stop is dropped before a tag). Marker and HTML forms of the
same unit are formatted alike.
"""

from __future__ import annotations

import pytest

from state.models import SegmentStatus, StateDocument, TranslationRecord
from translation.polish import polish_state, polish_text


def test_href_fragment_is_left_alone_while_text_is_formatted():
    assert polish_text('<a href="chapter.xhtml#中文2024">见第3章</a>') == (
        '<a href="chapter.xhtml#中文2024">见第 3 章</a>'
    )


@pytest.mark.parametrize(
    "markup",
    [
        '<img alt="图1(a)" src="a / b.png"/>',
        "<span title='“引用”中文'>",
        '<a title="1 > 0中文" href="#x">',
        '<span data-x="中文--英文"   class="a">',
        "<!-- 注释2024 a / b -->",
        "<![CDATA[中文2024]]>",
        "<?pi 中文2024?>",
    ],
)
def test_markup_is_returned_byte_for_byte(markup):
    out = polish_text(f"前文2024{markup}后文English")
    assert out == f"前文 2024{markup}后文 English"


@pytest.mark.parametrize("tag", ["code", "pre", "kbd", "samp", "script", "style"])
def test_code_and_non_text_content_is_not_formatted(tag):
    element = f'<{tag} class="c">x=中文2; a / b</{tag}>'
    assert polish_text(f"见代码3{element}中文") == f"见代码 3{element}中文"


@pytest.mark.parametrize(
    "markup",
    [
        "<code>外层<code>内层</code>中文2024 a / b</code>",
        "<pre><code>x=中文2</code>\n中文2024</pre>",
        "<?pi a > b 中文2024?>",
        "<script>if (a<b) { s = '中文2024'; }</script>",
    ],
)
def test_nested_code_and_pis_holding_a_closing_bracket_are_left_alone(markup):
    """The first </code> ended the protected span, and a processing instruction
    ended at its first ">", so the rest of each was formatted."""
    assert polish_text(f"见代码3{markup}后文2024") == f"见代码 3{markup}后文 2024"


@pytest.mark.parametrize(
    "markup",
    [
        '<!DOCTYPE x SYSTEM "a>中文2024 a / b">',
        "<!ENTITY e 'x>中文2024 a / b'>",
        '<!DOCTYPE x [<!ENTITY e "a>中文2024">]>',
    ],
)
def test_declarations_holding_a_quoted_closing_bracket_are_left_alone(markup):
    """A declaration ended at its first ">", quoted or not, so the rest of it
    was formatted."""
    assert polish_text(f"见3{markup}后文2024") == f"见 3{markup}后文 2024"


@pytest.mark.parametrize(
    "markup",
    [
        "<script><![CDATA[ s = '</script>中文2024 a / b'; ]]></script>",
        "<script><!-- s = '</script>中文2024 a / b'; --></script>",
    ],
)
def test_script_end_tag_inside_cdata_does_not_end_the_script(markup):
    """In XHTML a CDATA section or comment in a script is opaque, so the
    "</script>" in it is text; it ended the protected span and exposed the rest
    of the script to formatting."""
    assert polish_text(f"见代码3{markup}后文2024") == f"见代码 3{markup}后文 2024"


def test_unclosed_code_is_not_formatted():
    assert polish_text("见代码3<code>中文2024") == "见代码 3<code>中文2024"


def test_self_closing_code_does_not_protect_the_text_after_it():
    assert polish_text("<code/>中文2024<code>a</code>") == "<code/>中文 2024<code>a</code>"


def test_no_space_is_added_across_a_tag_boundary():
    assert polish_text("中文<em>English</em>中文") == "中文<em>English</em>中文"
    assert polish_text('中文<sup><a href="#n1">1</a></sup>') == '中文<sup><a href="#n1">1</a></sup>'


def test_whitespace_rules_still_act_across_a_tag_boundary():
    assert polish_text("改善。 <em>那个</em>") == "改善。<em>那个</em>"


def test_markers_survive_and_are_formatted_like_the_tags_they_stand_for():
    marked = "第3章⟦1⟧English⟦/1⟧中文⟦2⟧见注1"
    html = '第3章<em>English</em>中文<a id="n"/>见注1'
    assert polish_text(marked) == "第 3 章⟦1⟧English⟦/1⟧中文⟦2⟧见注 1"
    assert polish_text(html) == '第 3 章<em>English</em>中文<a id="n"/>见注 1'


def test_text_holding_the_stand_in_character_is_restored_exactly():
    text = "﷐中文2024<b>粗体</b>"
    assert polish_text(text) == "﷐中文 2024<b>粗体</b>"


@pytest.mark.parametrize(
    "text",
    [
        '<a href="chapter.xhtml#中文2024">见第3章</a>，学习machine learning。 好',
        "中文--英文<em>“引用”</em>中文 . . . 结束",
        "第3章⟦1⟧English⟦/1⟧中文（注释）--英文",
        "<code>x=中文2</code>占比5%甚至更多",
        "plain text with no CJK. . . done",
        '<!DOCTYPE x SYSTEM "a>中文2024">中文2024',
        "<script><![CDATA[ '</script>中文2024' ]]></script>中文2024",
    ],
)
def test_formatting_twice_is_formatting_once(text):
    once = polish_text(text)
    assert polish_text(once) == once


def test_format_command_state_pass_keeps_link_targets():
    """`tepub format` runs polish_state over stored translations."""
    state = StateDocument(
        segments={
            "s1": TranslationRecord(
                segment_id="s1",
                translation='见<a href="chapter.xhtml#中文2024">第3章</a>',
                status=SegmentStatus.COMPLETED,
            )
        },
        target_language="zh",
    )
    polished = polish_state(state)
    assert polished.segments["s1"].translation == '见<a href="chapter.xhtml#中文2024">第 3 章</a>'
