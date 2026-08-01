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
    """A refusal followed by a lot of text is still detected within the window.

    The fixture used to be a bare apology padded to length, which only matched
    because any apology counted as a refusal. That is the false positive this
    filter now avoids, so the fixture states an actual refusal instead; the
    behaviour under test — long input, still detected — is unchanged.
    """
    long_text = "I'm sorry, I cannot translate that." + " very" * 500
    assert looks_like_refusal(long_text)


def test_apology_without_refusal_is_not_flagged():
    """Ordinary prose that opens with an apology must survive."""
    assert not looks_like_refusal("I'm sorry for your loss, my friend.")
    assert not looks_like_refusal("抱歉，我来晚了。")


def test_chinese_refusal_variants_are_reachable():
    """The bare 抱歉，我 prefix used to shadow every longer variant."""
    assert looks_like_refusal("抱歉，我不能完成这个请求。")
    assert looks_like_refusal("抱歉，無法翻譯這段內容。")
