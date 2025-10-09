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


def test_polish_handles_percentage_spacing():
    """Test spacing with percentage symbols."""
    assert polish_translation("占人口比例的5%甚至更多") == "占人口比例的 5% 甚至更多"
    assert polish_translation("增长20%左右") == "增长 20% 左右"
    assert polish_translation("的15%是") == "的 15% 是"


def test_polish_handles_temperature_spacing():
    """Test spacing with temperature units."""
    # Unicode temperature symbols
    assert polish_translation("温度25℃很热") == "温度 25℃ 很热"
    assert polish_translation("约25℉左右") == "约 25℉ 左右"
    # Degree + letter combinations
    assert polish_translation("是25°C今天") == "是 25°C 今天"
    assert polish_translation("约25°c左右") == "约 25°c 左右"
    assert polish_translation("温度25°F较低") == "温度 25°F 较低"
    assert polish_translation("大约25°f吧") == "大约 25°f 吧"


def test_polish_handles_degree_spacing():
    """Test spacing with degree symbols."""
    assert polish_translation("角度45°比较") == "角度 45° 比较"
    assert polish_translation("转90°然后") == "转 90° 然后"


def test_polish_handles_permille_spacing():
    """Test spacing with per mille symbols."""
    assert polish_translation("浓度3‰的溶液") == "浓度 3‰ 的溶液"


def test_polish_handles_mixed_ellipsis_and_chinese_rules():
    """Test that ellipsis normalization works with other Chinese polishing rules."""
    # Ellipsis + spacing between Chinese and English
    assert polish_translation("学习 . . . machine learning") == "学习 ... machine learning"
    # Ellipsis + dash formatting
    assert polish_translation("文字 . . . 中文--英文") == "文字 ... 中文 —— 英文"
