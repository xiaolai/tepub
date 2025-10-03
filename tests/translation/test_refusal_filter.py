from translation.refusal_filter import looks_like_refusal


def test_detects_english_refusal():
    assert looks_like_refusal("I'm sorry, I can't help with that.")


def test_detects_chinese_refusal():
    assert looks_like_refusal("抱歉，我无法协助处理该内容。")


def test_ignores_legit_text():
    assert not looks_like_refusal("Sorry means something different in this context.")


def test_ignores_mid_sentence_apology():
    assert not looks_like_refusal("Well, I'm sorry but this part was already translated.")


def test_long_refusal_still_detected():
    long_text = "I'm sorry" + " very" * 500
    assert looks_like_refusal(long_text)
