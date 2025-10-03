from translation.languages import normalize_language, describe_language


def test_normalize_language_accepts_codes_and_names() -> None:
    assert normalize_language("en") == ("en", "English")
    assert normalize_language("English") == ("en", "English")
    assert normalize_language("zh-CN") == ("zh-CN", "Simplified Chinese")
    assert normalize_language("Simplified Chinese") == ("zh-CN", "Simplified Chinese")
    assert normalize_language("auto") == ("auto", "Auto")


def test_describe_language_falls_back_to_code() -> None:
    assert describe_language("en") == "English"
    assert describe_language("xx") == "xx"
