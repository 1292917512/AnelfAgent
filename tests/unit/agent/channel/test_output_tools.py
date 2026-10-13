"""统一输出工具（agent.channel.output_tools）目标 ID 类型容错单元测试。

覆盖场景：LLM 将纯数字 target_id 按 JSON number 传递时，
工具端应统一转 str 处理而非抛出 AttributeError。
"""

from __future__ import annotations

import pytest

import agent.channel.output_tools as output_tools
from agent.channel.output_tools import _normalize_target_id, _resolve_send_target


@pytest.mark.parametrize("raw", [None, "not json", {}, {"status": "queued"}, {"success": True, "outcome": "unknown"}])
def test_missing_receipt_is_unknown_and_not_retryable(raw):
    payload, ok = output_tools._check_send_result(raw, "qq", "123")
    assert not ok
    assert payload["success"] is False
    assert payload["outcome"] == "unknown"
    assert payload["retryable"] is False
    assert "先核验" in payload["hint"]


@pytest.mark.parametrize("raw", [{"ok": False}, {"error": "denied"}, {"success": True, "ok": False}])
def test_error_receipt_never_counts_as_success(raw):
    payload, ok = output_tools._check_send_result(raw, "qq", "123")
    assert not ok
    assert payload["success"] is False


@pytest.mark.parametrize("raw", [{"success": True}, {"ok": True}])
def test_explicit_receipt_is_normalized(raw):
    payload, ok = output_tools._check_send_result(raw, "qq", "123")
    assert ok
    assert payload["success"] is True


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


@pytest.mark.parametrize("scope,adapter,target,kind,session", [
    ("user_webui:1#chat-a", "webui", "1", "private", "chat-a"),
    ("group_qq:1", "qq", "1", "group", ""),
    ("user_telegram:1", "telegram", "1", "private", ""),
])
async def test_auto_text_uses_same_pipeline_and_live_history(sqlite, monkeypatch, scope, adapter, target, kind, session):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agent.channel.channel_types import ChannelStatus
    from agent.channel.delivery import capture_deliveries, delivery_count, delivery_tags
    from agent.mind.tool_activation import bind_scope, reset_scope
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageDomain, StorageRouter
    from core.tags import etag_all

    channel = SimpleNamespace(status=ChannelStatus.RUNNING, send_text=AsyncMock(return_value={"ok": True}),
                              conversation_scope_for_target=lambda tid, ct: None)
    monkeypatch.setattr(output_tools, "_get_channel", lambda cid: channel if cid == adapter else None)
    monkeypatch.setattr(output_tools, "_speak_if_on_call", AsyncMock(return_value=""))
    data = ConversationData(StorageRouter(sqlite=sqlite))
    monkeypatch.setattr(output_tools, "conversation_data_port", SimpleNamespace(bound=True, get=lambda: data))
    monkeypatch.setattr("agent.channel.outbound_guard._recent", {})
    scope_type, scope_id = scope.split("_", 1)
    await data.router.append(StorageDomain.CONVERSATION, scope_type=scope_type, scope_id=scope_id,
                             role="user", content="问题")
    await sqlite.fetch_live_conversation(scope_type=scope_type, scope_id=scope_id, limit=10)
    token = bind_scope(scope)
    try:
        with capture_deliveries(scope):
            result = json.loads(await output_tools.send_text_to_channel(adapter, scope,
                delivery_tags("internal", "pending", scope) + "答案[at_uid:1]", session_id=session, delivery_mode="auto"))
            assert delivery_count() == 1
    finally:
        reset_scope(token)
    kwargs = {"channel_type": kind, **({"session_id": session} if session else {})}
    channel.send_text.assert_awaited_once_with(target, "答案[at_uid:1]", **kwargs)
    assert dict(etag_all(result["delivery"])) == {"delivery_mode": "auto", "delivery_status": "sent", "delivery_scope": scope}
    rows = await sqlite.fetch_live_conversation(scope_type=scope_type, scope_id=scope_id, limit=10)
    assert [(row["role"], row["content"]) for row in rows] == [("user", "问题"), ("assistant", "答案[at_uid:1]")]
