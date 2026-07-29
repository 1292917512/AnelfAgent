"""多会话隔离（session scope）单元测试。

覆盖：
- entity_scope 子会话后缀规则与 parse_entity_scope 解析
- PFC 队列按 scope 隔离（同 uid 多会话各自入队）与未读计数
- resolve_reply_target 的 session_id 传播
- llm_client._adapt_messages 合并头部 system 时保留 cache_control 断点
- build_execution_context 的会话通知块
- switch_session 工具将目标会话排入回复队列
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.messages import parse_entity_scope
from agent.messages.presets import MessageAssistant, MessageGroupUser, MessageUser
from agent.mind.prefrontal_cortex import PrefrontalCortex

# ==================================================================
# scope 后缀规则与解析
# ==================================================================

class TestScopeSuffix:
    def test_default_session_no_suffix(self) -> None:
        msg = MessageUser(uid="web_user", text_content="hi")
        assert msg.scope_id == "web_user"
        assert msg.entity_scope == "user_web_user"

    def test_sub_session_suffix(self) -> None:
        msg = MessageUser(uid="web_user", session_id="abc", text_content="hi")
        assert msg.scope_id == "web_user#abc"
        assert msg.entity_scope == "user_web_user#abc"

    def test_session_equal_uid_no_suffix(self) -> None:
        """telegram 私聊 session_id==uid 时不加后缀（DB 键连续）。"""
        msg = MessageUser(uid="123", session_id="123", text_content="hi")
        assert msg.entity_scope == "user_123"

    def test_group_session_equal_gid_no_suffix(self) -> None:
        """群聊 session_id==group_id 时不加后缀。"""
        msg = MessageGroupUser(uid="u1", group_id="456", session_id="456", text_content="hi")
        assert msg.entity_scope == "group_456"

    def test_group_sub_session_suffix(self) -> None:
        msg = MessageGroupUser(uid="u1", group_id="456", session_id="t1", text_content="hi")
        assert msg.scope_id == "456#t1"
        assert msg.entity_scope == "group_456#t1"

    def test_parse_entity_scope(self) -> None:
        assert parse_entity_scope("user_123") == ("user", "123", "")
        assert parse_entity_scope("group_456") == ("group", "456", "")
        assert parse_entity_scope("user_web_user#abc") == ("user", "web_user", "abc")
        assert parse_entity_scope("") == ("", "", "")
        assert parse_entity_scope("invalid") == ("", "", "")


# ==================================================================
# PFC 队列 scope 隔离与未读计数
# ==================================================================

def _pfc() -> PrefrontalCortex:
    entity = SimpleNamespace(
        add_conversations_num=lambda: 0,
        reset_conversations_num=lambda: None,
        personality={},
        uid=0,
    )
    return PrefrontalCortex(
        everything_data=SimpleNamespace(get_anything=AsyncMock(return_value=entity)),
    )


class TestPfcScopeQueues:
    async def test_same_uid_multi_session_isolated(self) -> None:
        """同一 uid 的两个子会话各自产生独立队列条目与未读计数。"""
        pfc = _pfc()
        await pfc.add_task(MessageUser(uid="web_user", session_id="a", text_content="hello a"))
        await pfc.add_task(MessageUser(uid="web_user", session_id="b", text_content="hello b"))
        await pfc.add_task(MessageUser(uid="web_user", session_id="b", text_content="again b"))

        scopes = [s for s, _u, _g, _p in pfc.peek_all_tasks()]
        assert "user_web_user#a" in scopes
        assert "user_web_user#b" in scopes
        assert len(scopes) == 2
        assert pfc.get_unread_count("user_web_user#a") == 1
        assert pfc.get_unread_count("user_web_user#b") == 2
        assert pfc.get_adapter_key("user_web_user#a") == ""

    async def test_consume_scope_clears_state(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageUser(
            uid="web_user", session_id="a", text_content="hi", adapter_key="webui",
        ))
        assert pfc.consume_scope_task("user_web_user#a") is True
        assert pfc.get_unread_count("user_web_user#a") == 0
        assert pfc.get_adapter_key("user_web_user#a") == ""
        assert pfc.peek_all_tasks() == []

    async def test_pop_returns_scope(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageUser(uid="web_user", session_id="a", text_content="hi"))
        scope = await pfc.pop_user_task()
        assert scope == "user_web_user#a"

    def test_temporary_bucketed_by_scope(self) -> None:
        """短期记忆按 scope 分桶：读本 scope = 全局桶 + 本桶，不串其他会话。"""
        pfc = _pfc()
        pfc.add_temporary({"role": "system", "content": "global"})
        pfc.add_temporary({"role": "system", "content": "for-a"}, scope="user_x#a")
        pfc.add_temporary({"role": "system", "content": "for-b"}, scope="user_x#b")

        a_contents = [m["content"] for m in pfc.get_temporary("user_x#a")]
        assert a_contents == ["global", "for-a"]
        all_contents = [m["content"] for m in pfc.temporary]
        assert sorted(all_contents) == ["for-a", "for-b", "global"]


# ==================================================================
# resolve_reply_target 的 session 传播
# ==================================================================

class TestResolveReplyTarget:
    async def test_session_propagates_to_reply_message(self) -> None:
        from agent.mind.tools.decision_executor import resolve_reply_target

        pfc = _pfc()
        await pfc.add_task(MessageUser(
            uid="web_user", session_id="tab1", text_content="hi", adapter_key="webui",
        ))
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())

        msg = resolve_reply_target(mind, "user_web_user#tab1")
        assert isinstance(msg, MessageAssistant)
        assert msg.session_id == "tab1"
        assert msg.adapter_key == "webui"
        assert msg.entity_scope == "user_web_user#tab1"
        # 消费后队列清空
        assert pfc.peek_all_tasks() == []


# ==================================================================
# _adapt_messages 保留 cache_control
# ==================================================================

class TestAdaptMessagesCacheControl:
    def _adapt(self, messages: list[dict]) -> list[dict]:
        from agent.llm.llm_client import LLMClient
        return LLMClient._adapt_messages(object(), messages)  # type: ignore[arg-type]

    def test_breakpoint_preserved_as_blocks(self) -> None:
        msgs = [
            {"role": "system", "content": "stable", "cache_control": {"type": "ephemeral"}},
            {"role": "system", "content": "context", "cache_control": {"type": "ephemeral"}},
            {"role": "system", "content": "volatile"},
            {"role": "user", "content": "hi"},
        ]
        out = self._adapt(msgs)
        assert out[0]["role"] == "system"
        blocks = out[0]["content"]
        assert isinstance(blocks, list)
        assert blocks[0] == {
            "type": "text", "text": "stable", "cache_control": {"type": "ephemeral"},
        }
        assert blocks[1] == {
            "type": "text", "text": "context", "cache_control": {"type": "ephemeral"},
        }
        assert blocks[2] == {"type": "text", "text": "volatile"}

    def test_no_breakpoint_string_merge_unchanged(self) -> None:
        msgs = [
            {"role": "system", "content": "a"},
            {"role": "system", "content": "b"},
            {"role": "user", "content": "hi"},
        ]
        out = self._adapt(msgs)
        assert out[0] == {"role": "system", "content": "a\n\nb"}

    def test_non_head_system_converted_to_user(self) -> None:
        msgs = [
            {"role": "system", "content": "a"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "mid"},
        ]
        out = self._adapt(msgs)
        assert out[2]["role"] == "user"


# ==================================================================
# 会话通知块
# ==================================================================

class TestSessionNotification:
    async def test_notification_block_with_unread(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageUser(
            uid="web_user", session_id="other", text_content="在吗",
            adapter_key="webui",
        ))
        current = MessageUser(uid="web_user", session_id="cur", text_content="hi")
        ctx = pfc.build_execution_context(
            [], time.time(), 0, anything=current, adapter_key="webui",
        )
        content = ctx["content"]
        assert "[会话通知]" in content
        assert "1 条未读" in content
        assert "在吗" in content
        assert "switch_session" in content

    def test_no_pending_hint(self) -> None:
        pfc = _pfc()
        ctx = pfc.build_execution_context([], time.time(), 0, anything=None)
        assert "[当前无外部消息]" in ctx["content"]


# ==================================================================
# switch_session 工具
# ==================================================================

class TestSwitchSession:
    async def test_switch_enqueues_target_scope(self) -> None:
        from agent.mind.tools import session_tools

        pfc = _pfc()
        await pfc.add_task(MessageUser(
            uid="web_user", session_id="tab9", text_content="来看看",
            adapter_key="webui",
        ))
        mind = SimpleNamespace(
            pfc=pfc,
            _active_scopes=set(),
            _channel_snapshots={},
            try_execute_mind=AsyncMock(),
        )
        session_tools.set_mind(mind)
        try:
            raw = await session_tools.switch_session("user_web_user#tab9", reason="测试")
            result = json.loads(raw)
            assert result["ok"] is True
            assert result["scope"] == "user_web_user#tab9"
            assert "user_web_user#tab9" in pfc.pending_user.seen
            mind.try_execute_mind.assert_called_once()
        finally:
            session_tools.set_mind(None)

    async def test_switch_unknown_scope_rejected(self) -> None:
        from agent.mind.tools import session_tools

        pfc = _pfc()
        mind = SimpleNamespace(
            pfc=pfc,
            _active_scopes=set(),
            _channel_snapshots={},
            try_execute_mind=AsyncMock(),
        )
        session_tools.set_mind(mind)
        try:
            raw = await session_tools.switch_session("user_nobody")
            result = json.loads(raw)
            assert "error" in result
        finally:
            session_tools.set_mind(None)

    async def test_list_sessions_reports_unread(self) -> None:
        from agent.mind.tools import session_tools

        pfc = _pfc()
        await pfc.add_task(MessageUser(
            uid="web_user", session_id="tab1", text_content="未读消息",
            adapter_key="webui",
        ))
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set(), _channel_snapshots={})
        session_tools.set_mind(mind)
        try:
            result = json.loads(await session_tools.list_sessions())
            assert result["session_count"] == 1
            entry = result["sessions"][0]
            assert entry["scope"] == "user_web_user#tab1"
            assert entry["unread"] == 1
            assert entry["channel"] == "webui"
        finally:
            session_tools.set_mind(None)
