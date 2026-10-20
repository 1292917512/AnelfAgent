"""同轮新消息完整并入、水位隔离与回执去重。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.mind.execution import messages as message_updates
from agent.mind.execution.messages import _merge_new_messages
from agent.mind.execution.state import ThinkMode


def _msgs(n: int, chars: int = 0) -> list:
    return [
        {"id": i + 1, "role": "user",
         "content": f"[time:t{i}] 消息{i}" + "长" * chars,
         "ts_ns": (i + 1) * 10}
        for i in range(n)
    ]


def _ctx(consumed: list) -> SimpleNamespace:
    pfc = SimpleNamespace(
        collect_images=lambda scope=None: [],
        collect_media=lambda scope=None: [],
        consume_scope_task=lambda scope, **kwargs: consumed.append(scope),
    )
    return SimpleNamespace(
        mode=ThinkMode.REPLY,
        anything=SimpleNamespace(entity_scope="user_web:u1"),
        mind=SimpleNamespace(pfc=pfc),
        tool_chain=[],
        execution_steps=[],
        current_scope="user_web:u1",
        guardrail=SimpleNamespace(reset=lambda: None),
        adapter_key="webui",
    )


def _state() -> SimpleNamespace:
    return SimpleNamespace(last_merged_ts=0, last_merged_id=0, iteration=0)


@pytest.fixture()
def fetch(monkeypatch):
    """替换 DB 查询为可控假数据，返回 (fetched_list_holder, calls)。"""
    holder = {"msgs": []}
    calls: list = []

    async def fake(mind, anything, since_ts, since_id=0):
        calls.append((since_ts, since_id))
        return holder["msgs"]

    monkeypatch.setattr(message_updates, "_fetch_new_messages", fake)
    return holder, calls


class TestMergeMessages:
    async def test_sent_messages_sync_once_without_duplicate_activity(self, fetch, monkeypatch) -> None:
        holder, _ = fetch
        receipt = "[delivery_mode:auto][delivery_status:unknown][delivery_scope:user_web:u1] 发送中断，请核对"
        holder["msgs"] = [
            {"id": 1, "ts_ns": 10, "role": "system", "content": receipt},
            {"id": 2, "ts_ns": 20, "role": "assistant", "content": "实际发送的回复"},
            {"id": 3, "ts_ns": 30, "role": "user", "content": "[time:now] 新消息"},
        ]
        ctx, state = _ctx([]), _state()
        ctx.tool_chain.append({"role": "system", "content": receipt, "_source": {"origin": "reply_delivery"}})
        observe = AsyncMock()
        monkeypatch.setattr("agent.context.observation.emit_context_update", observe)
        assert await _merge_new_messages(ctx, state)
        assert [m["content"] for m in ctx.tool_chain] == [receipt, "实际发送的回复", "[time:now] 新消息"]
        assert observe.await_args.args[0] == [holder["msgs"][-1]]
        assert state.last_merged_id == 3 and state.last_merged_ts == 30

    async def test_merges_all_and_consumes(self, fetch) -> None:
        holder, _ = fetch
        holder["msgs"] = _msgs(3)
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        await _merge_new_messages(ctx, state)
        assert [m["content"] for m in ctx.tool_chain] == [m["content"] for m in holder["msgs"]]
        assert state.last_merged_ts == 30 and state.last_merged_id == 3
        assert consumed == ["user_web:u1"]  # 全量并入：消费待处理队列

    @pytest.mark.parametrize(("count", "chars"), [(50, 0), (3, 25000)])
    async def test_backlog_never_defers_the_latest_correction(self, fetch, count, chars) -> None:
        holder, calls = fetch
        holder["msgs"] = _msgs(count, chars)
        holder["msgs"][-1]["content"] = "[time:now] 先不要执行，我需要修改要求"
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        assert await _merge_new_messages(ctx, state)
        assert [m["content"] for m in ctx.tool_chain] == [m["content"] for m in holder["msgs"]]
        assert state.last_merged_id == count
        assert consumed == ["user_web:u1"]
        holder["msgs"] = []
        assert not await _merge_new_messages(ctx, state)
        assert calls[-1] == (count * 10, count)
        assert len(ctx.tool_chain) == count
