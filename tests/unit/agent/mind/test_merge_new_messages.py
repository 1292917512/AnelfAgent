"""循环期间新消息并入上限（_merge_new_messages）单元测试。

消息洪峰（群聊刷屏/离线堆积/连发长文）分批并入：单轮 ≤ 条数/字符上限，
超出部分不推进水位、不消费待处理队列，下轮/下一周期接续（保回复语义）。
"""

from types import SimpleNamespace

import pytest

from agent.mind.tools import round_helpers
from agent.mind.tools.round_helpers import _merge_new_messages
from agent.mind.tools.think_loop import ThinkMode


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
        consume_scope_task=lambda scope: consumed.append(scope),
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

    monkeypatch.setattr(round_helpers, "_fetch_new_user_messages", fake)
    return holder, calls


class TestMergeCaps:
    async def test_under_cap_merges_all_and_consumes(self, fetch) -> None:
        holder, _ = fetch
        holder["msgs"] = _msgs(3)
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        await _merge_new_messages(ctx, state)
        assert [m["content"] for m in ctx.tool_chain] == [m["content"] for m in holder["msgs"]]
        assert state.last_merged_ts == 30 and state.last_merged_id == 3
        assert consumed == ["user_web:u1"]  # 全量并入：消费待处理队列

    async def test_count_cap_defers_remainder(self, fetch, monkeypatch) -> None:
        holder, _ = fetch
        holder["msgs"] = _msgs(5)
        monkeypatch.setattr(round_helpers, "_MAX_MERGED_MESSAGES", 3)
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        await _merge_new_messages(ctx, state)
        assert len(ctx.tool_chain) == 3
        assert state.last_merged_ts == 30 and state.last_merged_id == 3  # 水位只到已并入
        assert consumed == []  # 有留存：保留待处理队列，循环结束另起周期
        assert any("留存" in s for s in ctx.execution_steps)

    async def test_char_cap_defers_but_first_always_merges(self, fetch, monkeypatch) -> None:
        """字符上限：首条强制并入保证进度（单条超预算也不拆分用户原话）。"""
        holder, _ = fetch
        holder["msgs"] = _msgs(3, chars=60)  # 每条约 70+ 字符
        monkeypatch.setattr(round_helpers, "_MAX_MERGED_CHARS", 100)
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        await _merge_new_messages(ctx, state)
        assert len(ctx.tool_chain) == 1  # 首条并入，第二条起超预算留存
        assert state.last_merged_id == 1
        assert consumed == []

    async def test_next_round_continues_from_watermark(self, fetch, monkeypatch) -> None:
        """留存消息在下轮从推进后的水位接续并入，全部并入后才消费队列。"""
        holder, calls = fetch
        holder["msgs"] = _msgs(5)
        monkeypatch.setattr(round_helpers, "_MAX_MERGED_MESSAGES", 3)
        consumed: list = []
        ctx, state = _ctx(consumed), _state()
        await _merge_new_messages(ctx, state)
        # 第二轮：DB 按新水位返回留存的两条
        holder["msgs"] = holder["msgs"][3:]
        await _merge_new_messages(ctx, state)
        assert calls[1] == (30, 3)  # 水位已推进到第一批末尾
        assert len(ctx.tool_chain) == 5
        assert state.last_merged_ts == 50 and state.last_merged_id == 5
        assert consumed == ["user_web:u1"]
