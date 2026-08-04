"""聊天历史清洗（web.routers.chat._clean_message）单元测试。

重点回归：执行摘要正文含冒号/JSON 数组时，
kind 判定与内容完整性不被标签剥离正则破坏。
"""

from __future__ import annotations

from web.routers.chat import _clean_message

_SUMMARY = (
    "[time:2026年08月03日20时39分22秒][uid:web_user][channel:webui] "
    "[已执行操作摘要] 本轮共执行 3 次工具\n"
    "  #1 memorize(content=【NAS .38更新】主人决定暂不折腾，状态：MAC 24:97:ed:2c:a3:6c 无法连接, "
    "tags=type:event,user:1292917512, importance=0.8) → "
    '{"ok": true, "id": 6330, "tags": ["type:event", "user:1292917512"]}\n'
    "  #2 send_message(channel_id=webui, target_id=web_user) → 已发送 -> web_user\n"
    "  #3 end_reply(reason=已回复主人) → {\"ok\": true, \"action\": \"end_reply\"}"
)


class TestCleanMessageKind:
    def test_tool_summary_kind_detected(self) -> None:
        result = _clean_message({"role": "system", "content": _SUMMARY})
        assert result["kind"] == "tool_summary"

    def test_tool_summary_content_intact(self) -> None:
        """回归：正文中的 MAC 冒号/JSON tags 数组不得吞掉摘要前缀与条目。"""
        result = _clean_message({"role": "system", "content": _SUMMARY})
        content = result["content"]
        assert content.startswith("[已执行操作摘要] 本轮共执行 3 次工具")
        assert "#1 memorize" in content
        assert "#2 send_message" in content
        assert "#3 end_reply" in content
        assert "24:97:ed:2c:a3:6c" in content

    def test_system_notice_kind(self) -> None:
        result = _clean_message({"role": "system", "content": "[系统] 计划已完成"})
        assert result["kind"] == "system_notice"

    def test_execution_steps_kind(self) -> None:
        result = _clean_message({"role": "system", "content": "[执行步骤]\n1. 检索记忆"})
        assert result["kind"] == "system_notice"

    def test_plain_message_no_kind(self) -> None:
        result = _clean_message({"role": "assistant", "content": "普通回复"})
        assert "kind" not in result
        assert result["content"] == "普通回复"

    def test_meta_tags_stripped_from_summary(self) -> None:
        result = _clean_message({"role": "system", "content": _SUMMARY})
        assert "[time:" not in result["content"]
        assert "[uid:" not in result["content"]


class TestCleanMessageTs:
    def test_ts_ns_seconds_to_epoch(self) -> None:
        result = _clean_message({"role": "user", "content": "hi", "ts_ns": 1785900000})
        assert result["ts"] == 1785900000
        assert result["timestamp"]

    def test_ts_ns_nanoseconds_to_epoch(self) -> None:
        result = _clean_message({"role": "user", "content": "hi", "ts_ns": 1785900000_000_000_000})
        assert result["ts"] == 1785900000

    def test_missing_ts_ns_no_ts(self) -> None:
        result = _clean_message({"role": "user", "content": "hi"})
        assert "ts" not in result
        assert "timestamp" not in result
