from translation.polish import polish_translation, target_is_chinese


def test_polish_inserts_spaces_between_chinese_and_numbers():
    assert polish_translation("第3章") == "第 3 章"


def test_polish_handles_english_phrase():
    assert polish_translation("学习machine learning方法") == "学习 machine learning 方法"


def test_polish_formats_quotes():
    assert polish_translation("中文“引用”中文") == "中文 “引用” 中文"


def test_polish_formats_dashes():
    assert polish_translation("中文--英文") == "中文 —— 英文"
    # No space after ）
    assert polish_translation("（注释）--英文") == "（注释）—— 英文"
    # No space before （
    assert polish_translation("中文--（注释）") == "中文 ——（注释）"
    # Both rules apply with Chinese context
    assert polish_translation("中文）--（中文") == "中文）——（中文"
    # Inside parentheses gets normal spacing
    assert polish_translation("（括号--内容）") == "（括号 —— 内容）"


def test_polish_fixes_existing_emdash_spacing():
    """Test fixing spacing around existing —— characters."""
    # Add spaces where missing
    assert polish_translation("中文——英文") == "中文 —— 英文"
    # Remove extra spaces and normalize
    assert polish_translation("中文  ——  英文") == "中文 —— 英文"
    # No space after ）
    assert polish_translation("（注释）——英文") == "（注释）—— 英文"
    # No space before （
    assert polish_translation("中文——（注释）") == "中文 ——（注释）"
    # Both rules with existing em-dash
    assert polish_translation("中文）——（中文") == "中文）——（中文"
    # Real example from translation
    assert polish_translation("优素福·阿萨尔·亚萨尔——阿拉伯最后一位统治的犹太国王") == "优素福·阿萨尔·亚萨尔 —— 阿拉伯最后一位统治的犹太国王"


def test_target_is_chinese():
    assert target_is_chinese("Simplified Chinese")
    assert target_is_chinese("中文")
    assert not target_is_chinese("English")


def test_polish_normalizes_ellipsis_in_chinese():
    """Test ellipsis normalization in Chinese text."""
    assert polish_translation("文字 . . . 更多文字") == "文字 ... 更多文字"
    assert polish_translation("开始 . . . . 结束") == "开始 ... 结束"
    assert polish_translation("已经正确...的格式") == "已经正确... 的格式"


def test_polish_normalizes_ellipsis_in_english():
    """Test ellipsis normalization in non-Chinese text."""
    assert polish_translation("Text before . . . and after.") == "Text before ... and after."
    assert polish_translation("Multiple . . . . dots here.") == "Multiple ... dots here."
    assert polish_translation("Already correct... format.") == "Already correct... format."


def test_polish_handles_mixed_ellipsis_and_chinese_rules():
    """Test that ellipsis normalization works with other Chinese polishing rules."""
    # Ellipsis + spacing between Chinese and English
    assert polish_translation("学习 . . . machine learning") == "学习 ... machine learning"
    # Ellipsis + dash formatting
    assert polish_translation("文字 . . . 中文--英文") == "文字 ... 中文 —— 英文"
