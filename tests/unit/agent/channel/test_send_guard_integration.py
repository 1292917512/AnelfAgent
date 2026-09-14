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
