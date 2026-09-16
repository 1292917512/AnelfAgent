"""发送管道出站哨兵集成测试（execute_send_action 挂拦截点）。

覆盖：反思上下文命中拦截时不触达频道；放行路径正常发送并登记记录；
回复上下文不受限。
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
    monkeypatch.setattr(output_tools, "_pending_settler", None)

    # 频道校验与目标解析替身化：测试只关心哨兵分支
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


async def _send(content: str = "hello") -> tuple[str, _Channel]:
    channel = _Channel()

    async def _invoke(_ch, target_id: str, channel_type: str):
        return await channel.send_text(target_id, content, channel_type=channel_type)

    result = await execute_send_action(
        channel_id="qq", target_id="1292917512",
        operation="消息", invoke=_invoke,
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

    async def test_reflect_blocked_by_recent_other_thinker(self) -> None:
        outbound_guard.note_outbound(_SCOPE, "user_qq:1292917512", "回复刚交付")
        token = bind_scope("reflect:bbbb2222")
        try:
            result, ch = await _send("重复代答")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["guard"] == "recent_outbound"
        assert ch.sent == []

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

    async def test_reply_context_unaffected(self) -> None:
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


class TestCrossSessionSettlement:
    """跨会话代答结算：回复周期向其他会话发送成功后消费其待处理条目。

    链间上下文互不可见，"已代答"事实只能经共享队列传递——否则周期末
    调度器按待处理事实再派正式回复，同一会话双份。
    """

    @staticmethod
    def _bind_settler(monkeypatch: pytest.MonkeyPatch, consumed: list[str]) -> None:
        def _settle(scope: str) -> bool:
            consumed.append(scope)
            return True

        monkeypatch.setattr(output_tools, "_pending_settler", _settle)

    async def test_reply_cross_session_settles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        consumed: list[str] = []
        self._bind_settler(monkeypatch, consumed)
        token = bind_scope("user_qq:999888777")
        try:
            result, ch = await _send("代答内容")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["代答内容"]
        assert consumed == [_SCOPE]
        assert payload["settled_pending"] == _SCOPE
        assert "switch_session" in payload["settled_note"]

    async def test_reply_same_session_not_settled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同会话多段回复是合法形态，不触发结算。"""
        consumed: list[str] = []
        self._bind_settler(monkeypatch, consumed)
        token = bind_scope(_SCOPE)
        try:
            result, _ch = await _send("多段回复")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert consumed == []
        assert "settled_pending" not in payload

    async def test_reflect_not_settled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """任务/反思链的发送是主动通知（提醒/汇报），不等价于回复待处理。"""
        consumed: list[str] = []
        self._bind_settler(monkeypatch, consumed)
        token = bind_scope("reflect:eeee5555")
        try:
            result, _ch = await _send("定时提醒")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert consumed == []
        assert "settled_pending" not in payload

    async def test_system_path_not_settled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """系统路径（重启通知/推送）不吞用户待回复。"""
        consumed: list[str] = []
        self._bind_settler(monkeypatch, consumed)
        result, _ch = await _send("系统通知")
        payload = json.loads(result)
        assert payload["success"] is True
        assert consumed == []

    async def test_settler_failure_does_not_break_send(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(_scope: str) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setattr(output_tools, "_pending_settler", _boom)
        token = bind_scope("user_qq:999888777")
        try:
            result, ch = await _send("正常发送")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert ch.sent == ["正常发送"]
        assert "settled_pending" not in payload

    async def test_no_pending_entry_no_settle_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """目标会话无待处理条目时结算静默（主动转发合法，返回值不加字段）。"""
        consumed: list[str] = []

        def _settle(scope: str) -> bool:
            consumed.append(scope)
            return False

        monkeypatch.setattr(output_tools, "_pending_settler", _settle)
        token = bind_scope("user_qq:999888777")
        try:
            result, _ch = await _send("帮我转发的通知")
        finally:
            reset_scope(token)
        payload = json.loads(result)
        assert payload["success"] is True
        assert "settled_pending" not in payload
