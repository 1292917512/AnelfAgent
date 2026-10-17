"""跨频道并发回复不割裂的链路验证。

场景：同一时刻 A 频道（QQ 群）与 B 频道（WebUI 私聊）各来一条消息——
两个 scope 必须各自独立入队、各自解析出正确的频道路由，互不串台。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.messages.presets import MessageGroupUser, MessageUser
from agent.mind.decisions import resolve_reply_target
from agent.mind.prefrontal_cortex import PrefrontalCortex


@pytest.mark.parametrize("fail_inner", [False, True])
async def test_nested_internal_space_clears_and_restores_channel_without_cross_task_leaks(fail_inner):
    from agent.channel.context import bind_current_channel, get_current_channel, reset_current_channel
    from agent.mind.think_session import think_session
    from agent.mind.tool_activation import ToolActivationManager

    parent_scope = ToolActivationManager.current_scope()
    parent = bind_current_channel(None)
    try:
        async def run(scope, channel):
            cleanup = []
            tools = SimpleNamespace(clear_dynamic_tools=lambda **kwargs: cleanup.append(
                (kwargs, get_current_channel(), ToolActivationManager.current_scope())))
            with think_session(tools, scope):
                assert get_current_channel() == channel
                with pytest.raises(RuntimeError, match="inner failed") if fail_inner else nullcontext():
                    with think_session(tools, "reflect:internal", with_token=False):
                        await asyncio.sleep(0)
                        assert get_current_channel() is None
                        assert ToolActivationManager.current_scope() == "reflect:internal"
                        if fail_inner:
                            raise RuntimeError("inner failed")
                assert get_current_channel() == channel
                assert ToolActivationManager.current_scope() == scope
                assert cleanup == [({"scope": "reflect:internal", "force": True}, channel, scope)]
            assert get_current_channel() is None
            assert cleanup[-1] == ({"scope": scope, "force": False}, None, parent_scope)

        await asyncio.gather(run("user_qq:1", "qq"), run("user_webui:1#a", "webui"))
    finally:
        reset_current_channel(parent)


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


class TestCrossChannelIsolation:
    async def test_late_message_preserves_latest_preview_and_earliest_unread(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageUser(uid="1", adapter_key="webui", session_id="a",
                                      text_content="latest", adapter_message_id="new", created_ts_ns=200))
        await pfc.add_task(MessageUser(uid="1", adapter_key="webui", session_id="a",
                                      text_content="older", adapter_message_id="old", created_ts_ns=100))
        signal = pfc.get_pending_signal("user_webui:1#a")
        assert signal.preview == "latest" and signal.message_id == "new"
        assert signal.created_ts_ns == 200 and signal.first_unread_ts_ns == 100
        assert pfc.get_unread_count("user_webui:1#a") == 2

    async def test_waiting_order_does_not_prioritize_private_over_older_mention(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageUser(uid="1", adapter_key="webui", text_content="new", created_ts_ns=200))
        await pfc.add_task(MessageGroupUser(uid="2", group_id="42", adapter_key="qq", to_me=True,
                                           text_content="old", created_ts_ns=100))
        assert [row[0] for row in pfc.peek_all_tasks()] == ["group_qq:42", "user_webui:1"]

    async def test_reply_target_keeps_diagnostic_message_id_out_of_model_and_reply_to(self) -> None:
        pfc = _pfc()
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="first", adapter_message_id="older",
        ))
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="second", adapter_message_id="latest",
        ))
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())
        target = resolve_reply_target(mind, "group_qq:100")
        assert target._trace_message_id == "latest"
        assert target.adapter_message_id == ""
        assert "latest" not in str(target.model_dump())
        assert "latest" not in str(target.get_agent_dic())
        assert pfc.get_pending_signal("group_qq:100") is None

    async def test_scopes_bucketed_by_adapter(self) -> None:
        """两频道消息进入各自的 scope 桶（adapter 维度隔离）。"""
        pfc = _pfc()
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="群消息",
        ))
        await pfc.add_task(MessageUser(
            uid="web_user", adapter_key="webui", text_content="私聊消息",
        ))

        scopes = [s for s, _u, _g, _p in pfc.peek_all_tasks()]
        assert "group_qq:100" in scopes
        assert "user_webui:web_user" in scopes
        # 群聊进 pending_group，私聊进 pending_user，互不混杂
        assert "group_qq:100" in pfc.pending_group.seen
        assert "user_webui:web_user" in pfc.pending_user.seen

    async def test_reply_targets_resolve_own_channel(self) -> None:
        """两个 scope 的回复目标各自解析出正确频道（不依赖共享路由表）。"""
        pfc = _pfc()
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="群消息",
        ))
        await pfc.add_task(MessageUser(
            uid="web_user", adapter_key="webui", text_content="私聊消息",
        ))
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())

        msg_group = resolve_reply_target(mind, "group_qq:100")
        assert msg_group is not None
        assert msg_group.adapter_key == "qq"
        assert msg_group.group_id == "100" or msg_group.group_id == 100

        # 第一个 scope 消费后，第二个 scope 不受影响
        msg_user = resolve_reply_target(mind, "user_webui:web_user")
        assert msg_user is not None
        assert msg_user.adapter_key == "webui"

        assert pfc.peek_all_tasks() == []

    async def test_scope_embedded_adapter_survives_routing_table_loss(self) -> None:
        """路由登记缺失时，scope 内嵌 adapter 仍能正确路由。

        覆盖进程重启后 replay/恢复场景：内存路由表为空，但 scope 自带频道信息。
        """
        pfc = _pfc()
        # 直接入队（绕过 add_task 的路由表登记）
        pfc.pending_user.append("user_telegram:456")
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())

        msg = resolve_reply_target(mind, "user_telegram:456")
        assert msg is not None
        assert msg.adapter_key == "telegram"

    async def test_cross_channel_snapshot_uses_adapter_scopes(self, monkeypatch) -> None:
        """跨频道快照按新格式 scope 记录（A 频道活跃可被 B 频道感知）。"""
        work = _pfc()
        clock = [time.time()]
        monkeypatch.setattr("agent.mind.prefrontal_cortex.time.time", lambda: clock[0])
        work.observe_channel(
            MessageUser(uid="123", adapter_key="qq", text_content="[to_me:1]在QQ说话"), window_minutes=30,
        )
        work.observe_channel(
            MessageUser(uid="web_user", adapter_key="webui", text_content="在Web说话"), window_minutes=30,
        )

        assert "user_qq:123" in work.channel_snapshots["qq"].active_scopes
        assert "user_webui:web_user" in work.channel_snapshots["webui"].active_scopes
        # 两频道快照互相独立
        assert "user_webui:web_user" not in work.channel_snapshots["qq"].active_scopes
        assert work.channel_snapshots["qq"].active_scopes["user_qq:123"].last_preview == "在QQ说话"
        clock[0] += 1801
        work.observe_channel(MessageUser(uid="456", adapter_key="qq", text_content="新话题"), window_minutes=30)
        assert set(work.channel_snapshots) == {"qq"}
        assert set(work.channel_snapshots["qq"].active_scopes) == {"user_qq:456"}
        assert not work.has_pending_tasks()


async def test_live_awareness_only_includes_unhandled_directed_messages(sqlite, monkeypatch):
    from agent.config import MindConfig
    from agent.mind.cross_channel import build_cross_channel_narrative
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageRouter

    mind = SimpleNamespace(config=MindConfig(), pfc=_pfc(), conversation_data=ConversationData(StorageRouter(sqlite=sqlite)))
    arrival = time.time_ns()

    async def arrive(message):
        nonlocal arrival
        arrival += 1
        message.created_ts_ns = arrival
        await mind.conversation_data.add_conversation_record_by_everything(message)
        await mind.pfc.add_task(message)
        return message

    old = await arrive(MessageGroupUser(uid="1", group_id="100", adapter_key="qq", to_me=True, text_content="已回答旧问题"))
    mind.pfc.consume_scope_task(old.entity_scope)
    await arrive(MessageGroupUser(uid="1", group_id="100", adapter_key="qq", text_content="普通群聊闲谈"))
    await arrive(MessageUser(uid="2", adapter_key="qq", message_kind="notification", text_content="非用户平台通知"))
    assert await build_cross_channel_narrative(mind, "user_webui:web_user#a") == []
    mention = await arrive(MessageGroupUser(uid="1", group_id="100", adapter_key="qq", to_me=True, text_content="请看新问题"))
    await arrive(MessageGroupUser(uid="2", group_id="100", adapter_key="qq", text_content="后来的群聊闲谈 [to_me:1]"))
    direct = await arrive(MessageUser(uid="1", adapter_key="telegram", text_content="新私聊问题：请解释 [push:notice]"))
    narrative = await build_cross_channel_narrative(mind, "user_webui:web_user#a")
    text = str(narrative)
    assert "请看新问题" in text and "新私聊问题" in text
    assert "群聊闲谈" not in text and "已回答旧问题" not in text and "非用户平台通知" not in text
    sources = [m["_source"] for m in narrative if m["_source"].get("id")]
    assert {(s["scope"], s["ts_ns"]) for s in sources} == {
        (mention.entity_scope, mention.created_ts_ns), (direct.entity_scope, direct.created_ts_ns)}
    assert mind.pfc.get_unread_count(mention.entity_scope) == 3
    assert not mind.pfc.consume_scope_task(mention.entity_scope, through_ts=mention.created_ts_ns)
    narrative = await build_cross_channel_narrative(mind, "user_webui:web_user#a")
    assert "请看新问题" not in str(narrative) and "新私聊问题" in str(narrative)
    mind.pfc.consume_scope_task(direct.entity_scope, through_ts=direct.created_ts_ns)
    assert await build_cross_channel_narrative(mind, "user_webui:web_user#a") == []
    await arrive(MessageGroupUser(uid="1", group_id="100", adapter_key="qq", to_me=True, text_content="再次提问"))
    assert "再次提问" in str(await build_cross_channel_narrative(mind, "user_webui:web_user#a"))


async def test_history_candidates_use_explicit_aliases_and_recent_topics(sqlite, monkeypatch):
    from agent.config import MindConfig
    from agent.mind.cross_channel import _recall_candidates
    from agent.mind.prefrontal_cortex import ChannelSnapshot, ScopeActivity
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageRouter

    await sqlite.set_alias(scope_type="user", scope_id="telegram:remote", primary_scope_type="user", primary_scope_id="qq:123")
    pfc = _pfc()
    pfc.channel_snapshots = {
        "webui": ChannelSnapshot(active_scopes={"user_webui:123#b": ScopeActivity(time.time(), "相关项目")}),
        "qq": ChannelSnapshot(active_scopes={"group_qq:5": ScopeActivity(time.time() - 7200, "过期话题")}),
    }
    mind = SimpleNamespace(config=MindConfig(), conversation_data=ConversationData(StorageRouter(sqlite=sqlite)), pfc=pfc)
    candidates = await _recall_candidates(mind, "group_qq:100", "user_qq:123")
    linked = {scope for scope, _, same_user in candidates if same_user}
    assert linked == {"user_qq:123", "user_telegram:remote"}
    assert "user_webui:123#b" in {scope for scope, _, _ in candidates}
    assert "group_qq:5" not in {scope for scope, _, _ in candidates}
    assert "user_webui:123#b" not in linked


@pytest.mark.parametrize("proceed", [False, True])
async def test_history_gate_uses_conversation_and_concurrent_searches(proceed, monkeypatch):
    from agent.config import MindConfig
    from agent.judgment.gate import WorkDecision
    from agent.mind.cross_channel import recall_cross_channel
    from agent.mind.prefrontal_cortex import ChannelSnapshot, ScopeActivity
    from core.hooks import hook_journal

    decision = AsyncMock(return_value=WorkDecision(proceed, "测试判断"))
    monkeypatch.setattr("agent.mind.cross_channel.assess_optional_work", decision)
    started = {name: asyncio.Event() for name in ("qq:1", "telegram:1")}

    async def search(kind, scope, vector, **kwargs):
        assert vector == [0.1, 0.2]
        started[scope].set()
        async with asyncio.timeout(2):
            await asyncio.gather(*(event.wait() for event in started.values()))
        if scope == "telegram:1":
            raise OSError("one source unavailable")
        return [{"content": "已在当前上下文的项目事实", "role": "assistant", "score": .99, "ts_ns": 1},
                {"content": "其他会话中的项目实施依据", "role": "user", "score": .9, "ts_ns": 2}]

    storage = SimpleNamespace(related_conversation_scopes=AsyncMock(return_value=[]),
                              search_conversation_vector=AsyncMock(side_effect=search))
    pfc = _pfc()
    pfc.channel_snapshots = {adapter: ChannelSnapshot(active_scopes={
        f"user_{adapter}:1": ScopeActivity(time.time(), "项目实施依据")}) for adapter in ("qq", "telegram")}
    mind = SimpleNamespace(config=MindConfig(), conversation_data=storage, pfc=pfc,
                          embedder=SimpleNamespace(available=True, embed_query=AsyncMock()))
    conversation = [{"role": "user", "content": "请帮我结合之前的记录完善项目实施方案"},
                    {"role": "assistant", "content": "已在当前上下文的项目事实"},
                    {"role": "tool", "content": "本轮检索缺少实施依据"}]
    future = asyncio.get_running_loop().create_future()
    future.set_result([0.1, 0.2])
    result = await recall_cross_channel(mind, conversation, "user_webui:other#a", query_vec_task=future)
    state = decision.call_args.args[2]
    assert state["current_scope"] == "user_webui:other#a"
    assert state["conversation"] == conversation[:2]
    assert all(message["role"] != "tool" for message in state["conversation"])
    mind.embedder.embed_query.assert_not_called()
    if proceed:
        assert storage.search_conversation_vector.await_count == 2
        assert "其他会话中的项目实施依据" in str(result)
        assert "已在当前上下文的项目事实" not in str(result)
        assert "[scope:user_qq:1][channel:qq][role:user][time:" in str(result)
    else:
        assert result == []
        storage.search_conversation_vector.assert_not_called()
    assert any(run["name"] == "memory.cross_channel" for run in hook_journal.snapshot()["recent"])


async def test_cancelling_cross_recall_preserves_shared_embedding(monkeypatch):
    from agent.config import MindConfig
    from agent.judgment.gate import WorkDecision
    from agent.mind.cross_channel import recall_cross_channel
    from agent.mind.prefrontal_cortex import ChannelSnapshot, ScopeActivity

    judged = asyncio.Event()

    async def judge(*args):
        judged.set()
        return WorkDecision(True, "需要相关历史")

    monkeypatch.setattr("agent.mind.cross_channel.assess_optional_work", judge)
    vector = asyncio.get_running_loop().create_future()
    pfc = _pfc()
    pfc.channel_snapshots = {"qq": ChannelSnapshot(active_scopes={"user_qq:1": ScopeActivity(time.time(), "项目")})}
    mind = SimpleNamespace(config=MindConfig(), embedder=SimpleNamespace(available=True), pfc=pfc,
        conversation_data=SimpleNamespace(related_conversation_scopes=AsyncMock(return_value=[])))
    task = asyncio.create_task(recall_cross_channel(
        mind, [{"role": "user", "content": "帮我查询之前项目实施方案的相关资料"}], "group_webui:1", query_vec_task=vector))
    try:
        async with asyncio.timeout(2):
            await judged.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not vector.cancelled()
    finally:
        vector.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
