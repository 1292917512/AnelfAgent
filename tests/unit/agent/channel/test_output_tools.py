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


class _FakeRouter:
    def __init__(self) -> None:
        self.appended: list[dict] = []

    async def append(self, _domain, **kwargs) -> None:
        self.appended.append(kwargs)


class _FakePort:
    bound = True

    def __init__(self, router: _FakeRouter) -> None:
        self._router = router

    def get(self):
        from types import SimpleNamespace
        return SimpleNamespace(router=self._router)


class TestRecordSentReply:
    async def test_channel_hook_overrides_scope(self, monkeypatch) -> None:
        """频道 scope 决议钩子命中时，assistant 记录写入钩子给出的规范 scope。

        回归背景：飞书 p2p 以 chat_id 为目标发送时曾记录到
        user_feishu:{chat_id}，与入站规范 scope 撕裂成两个会话。
        """
        from types import SimpleNamespace

        router = _FakeRouter()
        monkeypatch.setattr(output_tools, "conversation_data_port", _FakePort(router))
        hook_channel = SimpleNamespace(
            conversation_scope_for_target=lambda tid, ct: (
                "user", f"feishu:ou_peer#{tid}"
            ),
        )
        monkeypatch.setattr(output_tools, "_get_channel", lambda _cid: hook_channel)

        await output_tools._record_sent_reply(
            "oc_chat1", "回复内容", "private", adapter_key="feishu"
        )
        [entry] = router.appended
        assert entry["scope_type"] == "user"
        assert entry["scope_id"] == "feishu:ou_peer#oc_chat1"

    async def test_generic_rule_when_hook_returns_none(self, monkeypatch) -> None:
        """钩子返回 None 时回退通用规则（{adapter}:{target}[#{session}]）。"""
        from types import SimpleNamespace

        router = _FakeRouter()
        monkeypatch.setattr(output_tools, "conversation_data_port", _FakePort(router))
        plain_channel = SimpleNamespace(
            conversation_scope_for_target=lambda tid, ct: None,
        )
        monkeypatch.setattr(output_tools, "_get_channel", lambda _cid: plain_channel)

        await output_tools._record_sent_reply(
            "123", "回复内容", "private", session_id="chat9", adapter_key="webui"
        )
        [entry] = router.appended
        assert entry["scope_type"] == "user"
        assert entry["scope_id"] == "webui:123#chat9"

    async def test_hook_exception_falls_back(self, monkeypatch) -> None:
        """钩子异常不阻断记录，回退通用规则。"""
        from types import SimpleNamespace

        router = _FakeRouter()
        monkeypatch.setattr(output_tools, "conversation_data_port", _FakePort(router))

        def _boom(tid, ct):
            raise RuntimeError("hook exploded")

        monkeypatch.setattr(
            output_tools, "_get_channel",
            lambda _cid: SimpleNamespace(conversation_scope_for_target=_boom),
        )

        await output_tools._record_sent_reply("123", "回复内容", "group", adapter_key="qq")
        [entry] = router.appended
        assert entry["scope_type"] == "group"
        assert entry["scope_id"] == "qq:123"
