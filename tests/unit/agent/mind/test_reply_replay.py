"""keyless 快照回放测试 — 锁定回复主路径的行为回归（对齐 dsh keyless snapshot）。

无需 API key、不触网络：脚本化的 LLM 轮次队列即"录制"，测试驱动真实
think_loop 回放并断言三件套——
  1. sent_messages 布局（_layer 序列 / 消息数 / 角色序列）
  2. 工具调用序列（名称 + 顺序，经注册表执行替身捕获）
  3. 出站行为（发送管道监测：end_reply 静默收束不投递）

任何改动 think_loop / 上下文组装 / 出站路由的 PR，若改变了模型可见行为，
这里的断言会红——迫使作者在 PR 里解释"行为为什么该变"。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeContext,
    FakeMind,
    run_think_loop,
    text_result,
    tool_result,
)

from agent.messages import MessageUser


@pytest.mark.parametrize("failure", ["prepare", "after_tool", "audit_write"])
async def test_reply_failure_is_terminal_and_preserves_execution_facts(sqlite, monkeypatch, deliver_mock, failure):
    import asyncio
    from types import MethodType, SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from helpers.think_loop_fakes import current_fake_mind

    from agent.mind import cycle
    from agent.mind.autonomous import Decision, DecisionType, PendingMessage, SituationContext
    from agent.mind.decisions import execute_reply
    from agent.mind.execution import loop
    from agent.mind.mind import Mind
    from agent.mind.prefrontal_cortex import PrefrontalCortex
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageRouter
    from core.event_bus import EVENT_AFTER_REPLY, event_bus

    data = ConversationData(StorageRouter(sqlite=sqlite))
    message = MessageUser(uid="1", adapter_key="qq", adapter_message_id="request1", text_content="执行一次")
    await data.add_conversation_record_by_everything(message)
    mind = FakeMind(rounds=[tool_result("", ["recall"])], default_text=None)
    mind.conversation_data = data
    mind.pfc = PrefrontalCortex(SimpleNamespace())
    mind.pfc.pending_user.append(message.entity_scope)
    mind._active_scopes = set()
    mind._reply_activated_at = {}
    mind._reply_idle_event = asyncio.Event()
    mind._reset_phase_if_idle = Mock()
    mind._schedule_next_cycle = Mock()
    mind.note_activity = Mock()
    mind.reply = MethodType(Mind.reply, mind)
    mind._auto_cycle_retry = 2
    mind._reflecting = False
    mind.heartbeat_engine = SimpleNamespace(reflection_pending=False)
    mind.tool_assembly.clear_when_idle = Mock()
    prepare = AsyncMock(return_value=await data.get_conversation_record_by_everything(message))
    monkeypatch.setattr("agent.mind.execution.reply.coordinator.prepare_recollection", prepare)
    if failure == "prepare":
        prepare.side_effect = RuntimeError("context unavailable")
    if failure == "audit_write":
        monkeypatch.setattr(data, "append_context", AsyncMock(side_effect=OSError("audit unavailable")))
    if failure in {"after_tool", "audit_write"}:
        original = loop.prepare_round_context

        async def fail_after_tool(*args, **kwargs):
            if mind.executed_tools:
                raise RuntimeError("context unavailable after action")
            return await original(*args, **kwargs)

        monkeypatch.setattr(loop, "prepare_round_context", fail_after_tool)
    events = AsyncMock()
    monkeypatch.setattr(event_bus, "emit", events)
    monkeypatch.setattr(cycle, "_hb_write", Mock())
    end = {}
    decision = Decision(type=DecisionType.REPLY, target=message.entity_scope)
    token = current_fake_mind.set(mind)
    try:
        await cycle.execute_decisions(mind, end,
            SituationContext(pending_messages=[PendingMessage(scope=message.entity_scope, preview="执行一次")]), [decision])
    finally:
        current_fake_mind.reset(token)
    assert end["decisions_executed"] == [] and end["decisions_failed"] == ["reply"]
    assert mind._auto_cycle_retry == 2
    assert not mind.pfc.has_pending_tasks() and not mind._active_scopes
    assert mind._reply_idle_event.is_set()
    mind._schedule_next_cycle.assert_not_called()
    assert await execute_reply(mind, decision) is False
    assert await data.add_conversation_record_by_everything(
        message.model_copy(update={"created_ts_ns": message.created_ts_ns + 3_000_000_000})) is False
    prepare.assert_awaited_once()
    deliver_mock.assert_not_awaited()
    after = [call.args[1] for call in events.await_args_list if call.args[0] == EVENT_AFTER_REPLY]
    assert len(after) == 1 and after[0]["reason"] == "failed" and after[0]["error"]
    records = await sqlite.fetch_conversation(scope_type=message.scope_type, scope_id=message.scope_id, limit=30)
    if failure == "audit_write":
        from agent.mind.execution.recovery import recover_interrupted_replies

        assert await sqlite.load_reply_checkpoints()  # 记录失败时保留恢复依据
        assert len(await sqlite.load_reply_tool_journal(message.entity_scope)) == 1
        assert len(records) == 1
        assert await recover_interrupted_replies(data.router) == 1
        recovered = await sqlite.fetch_conversation(scope_type=message.scope_type, scope_id=message.scope_id, limit=30)
        assert "recall" in recovered[-1]["content"] and mind.executed_tools == ["recall"]
    else:
        assert await sqlite.load_reply_checkpoints() == []
        failed = [row["content"] for row in records if "[本轮执行失败]" in row["content"]]
        assert len(failed) == 1 and "未投递" in failed[0] and "不自动重试" in failed[0]
        if failure == "after_tool":
            assert mind.executed_tools == ["recall"]
            assert "recall" in failed[0]


class _ReplayMind(FakeMind):
    """脚本化 Mind 替身：按 _rounds 队列逐轮响应（轮次耗尽即失败）。"""

    def __init__(self, rounds: list) -> None:
        super().__init__(rounds=rounds, default_text=None, context=FakeContext(exec_layer=True))


@pytest.fixture
def replay_anything():
    return MessageUser(uid=1)


def _base() -> list:
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "user", "content": "你好", "_layer": "conversation"},
    ]


class TestScenarioPureText:
    """场景 1：纯文本独白 + end_reply 收敛。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([text_result("你好呀～")])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 布局：首轮调用，base(2) + exec_context(1)
        round1 = mind.sent_messages[0]
        assert [m.get("_layer") for m in round1[:2]] == ["stable", "conversation"]
        assert round1[-1].get("_layer") == "exec_context"
        # 无工具执行
        assert mind.executed_tools == []
        # 无可路由会话，不投递
        deliver_mock.assert_not_awaited()


class TestScenarioSingleTool:
    """场景 2：单工具调用后独白 + end_reply 收敛。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([
            tool_result("", ["recall"]),
            text_result("查到了"),
        ])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 两轮 LLM 调用
        assert len(mind.sent_messages) == 2
        # 工具序列：recall 恰好执行一次
        assert mind.executed_tools == ["recall"]
        # 第 2 轮上下文含第 1 轮的 assistant + tool 结果（工具链并入）
        roles2 = [m.get("role") for m in mind.sent_messages[1]]
        assert "tool" in roles2
        # 无可路由会话，不投递
        deliver_mock.assert_not_awaited()


class TestScenarioMultiToolChain:
    """场景 3：多轮工具链（顺序保持）。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([
            tool_result("", ["recall"]),
            tool_result("", ["get_conversation"]),
            text_result("都办好了"),
        ])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 三轮 LLM 调用
        assert len(mind.sent_messages) == 3
        # 工具序列按轮次顺序执行
        assert mind.executed_tools == ["recall", "get_conversation"]
        # 无可路由会话，不投递
        deliver_mock.assert_not_awaited()
