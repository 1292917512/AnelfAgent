"""发送管道集成测试（execute_send_action 挂载点）。

覆盖：执行期安全边界（reply_active 拦截不触达频道）；放行路径正常发送并登记；
本会话回复豁免；record_content 历史固化；空会话拦截。
决策面（近期出站/等价内容）由 context provider 注入覆盖，不在管道拦截。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent.channel.outbound_guard as outbound_guard
import agent.channel.output_tools as output_tools
from agent.channel.output_tools import execute_send_action
from agent.mind.tool_activation import bind_scope, reset_scope

_SCOPE = "user_qq:1292917512"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    outbound_guard._recent.clear()
    monkeypatch.setattr(outbound_guard, "_reply_scopes_provider", None)
    monkeypatch.setattr(outbound_guard, "get_config_bool", lambda key, default: True)
    monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 180)
    # 空会话判定默认失败放行（runtime 未就绪），单测按需覆写
    async def _fail_open(_scope: str):
        return None

    monkeypatch.setattr(outbound_guard, "target_has_interaction", _fail_open)

    # 频道校验与目标解析替身化：测试只关心安全边界分支
    monkeypatch.setattr(
        output_tools, "_validate_channel",
        lambda _cid: (SimpleNamespace(send_text=None), None),
    )
    monkeypatch.setattr(
        output_tools, "_resolve_send_target",
        lambda _cid, _tid: ("1292917512", "private"),
    )
    monkeypatch.setattr(output_tools, "_get_channel", lambda _ak: None)

    yield
    outbound_guard._recent.clear()


class _Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, target_id: str, content: str, **kwargs) -> str:
        self.sent.append(content)
        return json.dumps({"success": True, "message_id": "m1"})


async def _send(content: str = "hello", *, record_content: str = "") -> tuple[str, _Channel]:
    channel = _Channel()

    async def _invoke(_ch, target_id: str, channel_type: str):
        return await channel.send_text(target_id, content, channel_type=channel_type)

    result = await execute_send_action(
        channel_id="qq", target_id="1292917512",
        operation="消息", invoke=_invoke,
        outbound_preview=content[:80],
        record_content=record_content,
    )
    return result, channel


class TestGuardIntegration:
    async def test_reflect_blocked_by_active_reply(self) -> None:
        outbound_guard.bind_reply_scopes(lambda: frozenset({_SCOPE}))
        token = bind_scope("reflect:aaaa1111")
        try:
            result, ch = await _send("代答内容")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is False
        assert payload["guard"] == "reply_active"
        assert ch.sent == []
        # 拦截路径不登记出站记录
        assert _SCOPE not in outbound_guard._recent

    async def test_recent_outbound_no_longer_blocks(self) -> None:
        """窗口内其他链刚出站不再拦截（决策面在注入层），正常发送并登记。"""
        outbound_guard.note_outbound(_SCOPE, "reflect:xxxx9999", "刚已代答的内容")
        token = bind_scope("reflect:bbbb2222")
        try:
            result, ch = await _send("补充说明")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["补充说明"]

    async def test_reflect_allowed_when_clear(self) -> None:
        token = bind_scope("reflect:cccc3333")
        try:
            result, ch = await _send("正常任务推送")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["正常任务推送"]
        assert outbound_guard._recent[_SCOPE][-1].thinker == "reflect:cccc3333"

    async def test_owner_reply_exempt(self) -> None:
        """本会话回复（thinker == target_scope）豁免。"""
        outbound_guard.bind_reply_scopes(lambda: frozenset({_SCOPE}))
        outbound_guard.note_outbound(_SCOPE, "reflect:dddd4444", "任务刚发过")
        token = bind_scope(_SCOPE)
        try:
            result, ch = await _send("回复正文")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["回复正文"]


class TestRecordContent:
    async def test_record_content_persisted_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """record_content 非空时发送成功统一固化对话历史。"""
        recorded: list[tuple] = []

        async def _record(target_id, content, channel_type, session_id="", adapter_key=""):
            recorded.append((target_id, content, channel_type, adapter_key))

        monkeypatch.setattr(output_tools, "_record_sent_reply", _record)
        token = bind_scope("reflect:aaaa1111")
        try:
            result, _ = await _send("固化内容", record_content="固化内容")
        finally:
            reset_scope(token)
        assert json.loads(result)["success"] is True
        assert recorded == [("1292917512", "固化内容", "private", "qq")]

    async def test_no_record_when_content_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """record_content 为空时不固化（媒体无文本说明等场景由调用方决定）。"""
        recorded: list[tuple] = []

        async def _record(*args, **kwargs):
            recorded.append(args)

        monkeypatch.setattr(output_tools, "_record_sent_reply", _record)
        token = bind_scope("reflect:aaaa1111")
        try:
            result, _ = await _send("仅发送")
        finally:
            reset_scope(token)
        assert json.loads(result)["success"] is True
        assert recorded == []

    async def test_no_record_on_rejection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """被安全边界拦截时不固化历史（消息未发出）。"""
        recorded: list[tuple] = []

        async def _record(*args, **kwargs):
            recorded.append(args)

        monkeypatch.setattr(output_tools, "_record_sent_reply", _record)
        outbound_guard.bind_reply_scopes(lambda: frozenset({_SCOPE}))
        token = bind_scope("reflect:aaaa1111")
        try:
            result, _ = await _send("被拦截", record_content="被拦截")
        finally:
            reset_scope(token)
        assert json.loads(result)["success"] is False
        assert recorded == []


class TestEmptyConversationGuard:
    async def test_reply_context_blocked_on_empty_conversation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """空会话上即使回复上下文也不得发起对话（正常回复必有用户消息）。"""
        async def _empty(_scope: str):
            return False

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _empty)
        token = bind_scope(_SCOPE)
        try:
            result, ch = await _send("主动搭话")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is False
        assert payload["guard"] == "empty_conversation"
        assert ch.sent == []

    async def test_reflect_blocked_on_empty_conversation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """反思/任务上下文向空会话发送同样拒绝，错误结构化回传供模型自适应。"""
        async def _empty(_scope: str):
            return False

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _empty)
        token = bind_scope("reflect:eeee5555")
        try:
            result, ch = await _send("任务推送")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is False
        assert payload["guard"] == "empty_conversation"
        assert payload["retryable"] is False
        assert ch.sent == []

    async def test_interacted_target_allowed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """目标会话有过用户消息时放行。"""
        async def _interacted(_scope: str):
            return True

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _interacted)
        token = bind_scope("reflect:ffff6666")
        try:
            result, ch = await _send("正常推送")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["正常推送"]

    async def test_system_path_exempt(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """系统路径（无思维会话）不受空会话拦截（重启交接/实体推送等）。"""
        calls: list[str] = []

        async def _empty(scope: str):
            calls.append(scope)
            return False

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _empty)
        result, ch = await _send("系统通知")
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["系统通知"]
        assert calls == []

    async def test_query_failure_fails_open(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """历史查询失败按非空放行（行为护栏而非安全边界）。"""
        async def _unknown(_scope: str):
            return None

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _unknown)
        token = bind_scope(_SCOPE)
        try:
            result, ch = await _send("回复正文")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["回复正文"]

    async def test_disabled_config_allows(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """outbound_guard_empty_enabled 关闭时拦截不生效。"""
        async def _empty(_scope: str):
            return False

        monkeypatch.setattr(outbound_guard, "target_has_interaction", _empty)
        monkeypatch.setattr(
            outbound_guard, "get_config_bool",
            lambda key, default: key != "outbound_guard_empty_enabled",
        )
        token = bind_scope(_SCOPE)
        try:
            result, ch = await _send("回复正文")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["回复正文"]
