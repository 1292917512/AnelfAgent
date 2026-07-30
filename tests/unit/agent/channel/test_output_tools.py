"""统一输出工具（agent.channel.output_tools）目标 ID 类型容错单元测试。

覆盖场景：LLM 将纯数字 target_id 按 JSON number 传递时，
工具端应统一转 str 处理而非抛出 AttributeError。
"""

from __future__ import annotations

import agent.channel.output_tools as output_tools
from agent.channel.output_tools import _normalize_target_id, _resolve_send_target


class TestNormalizeTargetId:
    def test_int_input_converted_to_str(self) -> None:
        resolved, forced = _normalize_target_id(1292917512)  # type: ignore[arg-type]
        assert resolved == "1292917512"
        assert forced is None

    def test_str_input_passthrough(self) -> None:
        resolved, forced = _normalize_target_id("1292917512")
        assert resolved == "1292917512"
        assert forced is None

    def test_none_input_returns_empty(self) -> None:
        resolved, forced = _normalize_target_id(None)  # type: ignore[arg-type]
        assert resolved == ""
        assert forced is None

    def test_user_prefix(self) -> None:
        resolved, forced = _normalize_target_id("user:12345")
        assert resolved == "12345"
        assert forced == "private"

    def test_group_prefix(self) -> None:
        resolved, forced = _normalize_target_id("group:1104224649")
        assert resolved == "1104224649"
        assert forced == "group"


class TestResolveSendTarget:
    def test_int_target_id_no_crash(self, monkeypatch) -> None:
        monkeypatch.setattr(
            output_tools, "_resolve_channel_type", lambda _cid, _tid: "private",
        )
        final_id, channel_type = _resolve_send_target("qq", 1292917512)  # type: ignore[arg-type]
        assert final_id == "1292917512"
        assert channel_type == "private"

    def test_prefixed_int_like_string(self, monkeypatch) -> None:
        monkeypatch.setattr(
            output_tools, "_resolve_channel_type", lambda _cid, _tid: "private",
        )
        final_id, channel_type = _resolve_send_target("qq", "group:1104224649")
        assert final_id == "1104224649"
        assert channel_type == "group"
