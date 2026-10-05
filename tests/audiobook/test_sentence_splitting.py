"""Language-aware sentence splitting."""

from __future__ import annotations

import pytest

from audiobook.preprocess import split_sentences

pytestmark = pytest.mark.needs_punkt


def test_english_splits_on_terminators():
    assert split_sentences("First one. Second one! Third one?", language="en") == [
        "First one.",
        "Second one!",
        "Third one?",
    ]


@pytest.mark.parametrize("lang", ["zh", "zh-CN", "ja", "ko"])
def test_cjk_text_is_split(lang):
    """Punkt has no CJK model; the English tokenizer returned one giant sentence."""
    text = "这是第一句话。这是第二句话！这是第三句吗？"

    assert len(split_sentences(text, language=lang)) == 3


def test_unknown_language_falls_back_to_english():
    assert split_sentences("One. Two!", language="xx") == ["One.", "Two!"]


def test_empty_text_returns_empty_list():
    assert split_sentences("", language="en") == []


def test_cjk_split_keeps_decimals_and_abbreviations_whole():
    """An ASCII full stop inside CJK text is not a sentence end unless a space follows.

    Splitting on every "." turned 3.5 into "3." + "5..." and U.S.A. into three
    one-letter sentences, each spoken with a pause.
    """
    text = "版本3.5发布了。价格是3.5元；U.S.A.很大。Done. 最后一句！"
    assert split_sentences(text, language="zh") == [
        "版本3.5发布了。",
        "价格是3.5元；",
        "U.S.A.很大。",
        "Done.",
        "最后一句！",
    ]
