"""标签系统（core.tags）清洗函数单元测试。

重点回归：batch_remove_tags 不得跨 ``[``/``]``/换行配对，
否则多行正文（如执行摘要）会被整段吞掉。
"""

from __future__ import annotations

from core.tags import (
    batch_remove_tags,
    strip_functional_tags,
    strip_message_meta_tags,
)


class TestBatchRemoveTags:
    def test_simple_tag_keeps_value(self) -> None:
        assert batch_remove_tags("[think:这是思考]正文") == "这是思考正文"

    def test_value_with_colon(self) -> None:
        assert batch_remove_tags("[media_file:image:/tmp/a.png]") == "image:/tmp/a.png"

    def test_no_tag_unchanged(self) -> None:
        assert batch_remove_tags("普通文本 [无冒号方括号]") == "普通文本 [无冒号方括号]"

    def test_multiline_body_not_swallowed(self) -> None:
        """回归：多行正文中首个 [ 不得与远处的 : / ] 错误配对。"""
        text = (
            "[已执行操作摘要] 本轮共执行 2 次工具\n"
            "  #1 memorize(content=状态：MAC 24:97:ed:2c:a3:6c 无法连接) → {\"tags\": [\"type:event\"]}\n"
            "  #2 send_message(channel_id=webui) → 已发送"
        )
        result = batch_remove_tags(text)
        assert result.startswith("[已执行操作摘要] 本轮共执行 2 次工具")
        assert "#1 memorize" in result
        assert "#2 send_message" in result

    def test_json_array_not_treated_as_tag(self) -> None:
        text = '结果 {"tags": ["type:event", "user:123"]}'
        assert batch_remove_tags(text) == text

    def test_single_line_tag_still_stripped(self) -> None:
        """同一行内的合法标签仍正常剥离（值含冒号也完整保留）。"""
        assert batch_remove_tags("前 [key:va:ue] 后") == "前 va:ue 后"


class TestStripTags:
    def test_meta_tags_removed_entirely(self) -> None:
        text = "[time:2026年08月03日][uid:web_user] 正文"
        assert strip_message_meta_tags(text) == " 正文"

    def test_functional_tags_removed_entirely(self) -> None:
        text = "[media_file:image:/tmp/a.png]正文"
        assert strip_functional_tags(text) == "正文"

    def test_meta_strip_leaves_summary_prefix(self) -> None:
        text = "[time:2026年08月03日][channel:webui] [已执行操作摘要] 本轮共执行 1 次工具"
        assert strip_message_meta_tags(text).strip().startswith("[已执行操作摘要]")
