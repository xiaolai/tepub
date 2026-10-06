"""A translated HTML unit must keep its markup (decision D6).

The same tags and the same href, src, id and epub:type values, in any order:
another language may legitimately move a link or an emphasis. Ruby annotation
is exempt, since languages annotate differently.
"""

from __future__ import annotations

import pytest

from translation.markup import markup_mismatch

SOURCE = (
    'A claim<a epub:type="noteref" id="r1" href="notes.xhtml#n1"><sup>1</sup></a> '
    'with <em>emphasis</em> and <img src="i.png" alt="pic"/>.'
)


def test_a_faithful_translation_passes() -> None:
    reply = (
        "一个<em>强调的</em>论断"
        '<a epub:type="noteref" id="r1" href="notes.xhtml#n1"><sup>1</sup></a>'
        '，还有<img src="i.png" alt="图"/>。'
    )
    assert markup_mismatch(SOURCE, reply) is None


@pytest.mark.parametrize(
    ("reply", "complaint"),
    [
        ('Un argument avec <em>emphase</em> et <img src="i.png"/>.', "a"),
        (SOURCE.replace('id="r1"', 'id="x9"'), "r1"),
        (SOURCE.replace('src="i.png"', 'src="other.png"'), "i.png"),
        (SOURCE.replace("<sup>1</sup>", "1"), "sup"),
        (SOURCE.replace('href="notes.xhtml#n1"', 'href="#n1"'), "notes.xhtml#n1"),
    ],
    ids=["dropped link", "renamed id", "changed image", "dropped superscript", "changed href"],
)
def test_changed_markup_is_named(reply: str, complaint: str) -> None:
    problem = markup_mismatch(SOURCE, reply)
    assert problem is not None and complaint in problem


def test_table_structure_must_be_kept() -> None:
    source = "<tr><td>Year</td><td>Sales</td></tr><tr><td>2024</td><td>10k</td></tr>"
    merged = "<tr><td>年份</td><td>销量</td></tr><tr><td>2024 10k</td></tr>"
    assert markup_mismatch(source, merged) is not None


def test_ruby_annotation_may_differ() -> None:
    source = "<ruby>漢<rt>kan</rt></ruby>字を読む"
    reply = "Reading kanji"
    assert markup_mismatch(source, reply) is None


def test_plain_text_in_both_passes() -> None:
    assert markup_mismatch("Plain words.", "Mots simples.") is None


def test_emphasis_may_differ() -> None:
    """Measured on TranslateGemma, en to zh: every reply that failed after a retry
    had only lost or gained an <em>. Chinese often drops emphasis, and leaving
    the paragraph untranslated is worse than losing it."""
    assert markup_mismatch(SOURCE, SOURCE.replace("<em>emphasis</em>", "emphasis")) is None
    assert markup_mismatch("plain", "<b>plain</b>") is None


def test_a_line_break_may_be_lost() -> None:
    """Reordered into Chinese, "co-author of The Chinese Heroin Trade<br/>and
    author of ..." has no place left for its break; untranslated is worse."""
    source = (
        "Ko-lin Chin, co-author of The Chinese Heroin Trade<br/>and author of The Golden Triangle"
    )
    assert markup_mismatch(source, "《中国海洛因贸易》合著者、《金三角》作者陈国霖") is None
