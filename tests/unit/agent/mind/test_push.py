"""实体推送中枢（PushHub）与轮内弹窗（_merge_pushes）单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.mind.push import PushHub, _sanitize_source
from agent.mind.tools.round_helpers import ThinkMode, _merge_pushes
from core.tags import etag_all


class FakePFC:
    """enqueue_scope_reply 依赖的最小 PFC 替身。"""

    def __init__(self) -> None:
        self.temporary: list[tuple[dict, str]] = []
        self.pending_user: list[str] = []
        self.pending_group: list[str] = []
        self.previews: dict[str, str] = {}
        self.adapter_keys: dict[str, str] = {}
        self.consumed: list[str] = []

    def add_temporary(self, clip: dict, scope: str = "") -> None:
        self.temporary.append((clip, scope))

    def set_message_preview(self, scope: str, preview: str) -> None:
        self.previews[scope] = preview

    def set_adapter_key(self, scope: str, channel: str) -> None:
        self.adapter_keys[scope] = channel

    def consume_scope_task(self, scope: str) -> None:
        self.consumed.append(scope)


class FakeMind:
    def __init__(self) -> None:
        self.pfc = FakePFC()
        self.wakes = 0

    async def try_execute_mind(self) -> None:
        self.wakes += 1


_SCOPE = "user_webui:u1"


def test_push_wraps_tags_and_enqueues() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    ok = hub.push(_SCOPE, "voiceprint", "声纹库新增 3 条样本", channel="webui", trigger=False)
    assert ok is True

    clip, scope = mind.pfc.temporary[0]
    assert scope == _SCOPE
    assert clip["role"] == "system"
    content = clip["content"]
    assert content.startswith("[push:voiceprint][time:")
    assert content.endswith(" 声纹库新增 3 条样本")
    tags = dict(etag_all(content))
    assert tags["push"] == "voiceprint"
    assert "time" in tags

    assert mind.pfc.pending_user == [_SCOPE]
    assert mind.pfc.previews[_SCOPE].startswith("实体推送 voiceprint")
    assert mind.pfc.adapter_keys[_SCOPE] == "webui"
    assert hub.current_seq(_SCOPE) == 1


def test_push_group_scope_enqueues_pending_group() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push("group_qq:g1", "devops", "构建完成", trigger=False)
    assert mind.pfc.pending_group == ["group_qq:g1"]
    assert mind.pfc.pending_user == []


def test_push_invalid_scope_writes_global_bucket_only() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    ok = hub.push("", "watcher", "全局通知", trigger=False)
    assert ok is True
    clip, scope = mind.pfc.temporary[0]
    assert scope == ""
    assert "全局通知" in clip["content"]
    assert mind.pfc.pending_user == [] and mind.pfc.pending_group == []
    assert hub.current_seq("") == 0


def test_push_empty_content_rejected() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    assert hub.push(_SCOPE, "a", "  ", trigger=False) is False
    assert mind.pfc.temporary == []


def test_push_source_sanitized_for_tag_syntax() -> None:
    assert _sanitize_source("[we:ird]\n源") == "weird源"
    assert _sanitize_source("  ") == "entity"
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push(_SCOPE, "[a:b]", "内容", trigger=False)
    assert mind.pfc.temporary[0][0]["content"].startswith("[push:ab][time:")


def test_push_content_truncated() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push(_SCOPE, "a", "x" * 3000, trigger=False)
    content = mind.pfc.temporary[0][0]["content"]
    assert "…(截断)" in content
    assert len(content) < 3000


@pytest.mark.asyncio
async def test_push_trigger_wakes_mind() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push(_SCOPE, "a", "触发", trigger=True)
    await asyncio.sleep(0)
    assert mind.wakes == 1


@pytest.mark.asyncio
async def test_push_trigger_false_does_not_wake() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push(_SCOPE, "a", "静默", trigger=False)
    await asyncio.sleep(0)
    assert mind.wakes == 0


def test_drain_inflight_respects_watermark() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.push(_SCOPE, "a", "第一条", trigger=False)
    watermark = hub.current_seq(_SCOPE)
    hub.push(_SCOPE, "a", "第二条", trigger=False)

    texts = hub.drain_inflight(_SCOPE, since=watermark)
    assert len(texts) == 1 and "第二条" in texts[0]
    # 一次性消费：再次 drain 为空
    assert hub.drain_inflight(_SCOPE, since=watermark) == []

    # 水位内的推送（已随短期记忆进 base 快照）被丢弃，不重复注入
    hub.push(_SCOPE, "a", "第三条", trigger=False)
    assert hub.drain_inflight(_SCOPE, since=hub.current_seq(_SCOPE)) == []


@pytest.mark.asyncio
async def test_push_cross_thread_dispatches_to_bound_loop() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    hub.bind_loop(asyncio.get_running_loop())
    ok = await asyncio.to_thread(hub.push, _SCOPE, "watcher", "线程推送", "", False)
    assert ok is True
    await asyncio.sleep(0.05)  # call_soon_threadsafe 回主循环执行
    assert mind.pfc.temporary[0][1] == _SCOPE
    assert "线程推送" in mind.pfc.temporary[0][0]["content"]


def _make_ctx(mind: FakeMind, scope: str) -> SimpleNamespace:
    return SimpleNamespace(
        mode=ThinkMode.REPLY,
        anything=object(),
        mind=mind,
        current_scope=scope,
        tool_chain=[],
        execution_steps=[],
    )


@pytest.mark.asyncio
async def test_merge_pushes_injects_into_tool_chain() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    mind.push_hub = hub
    hub.push(_SCOPE, "devops", "构建完成", trigger=False)

    ctx = _make_ctx(mind, _SCOPE)
    state = SimpleNamespace(iteration=0, push_watermark=0)
    await _merge_pushes(ctx, state)

    assert len(ctx.tool_chain) == 1
    body = ctx.tool_chain[0]["content"]
    assert ctx.tool_chain[0]["role"] == "system"
    assert "[实体推送]" in body
    assert "[push:devops]" in body
    assert "非用户消息" in body
    # 注入后消费待处理队列，避免空转一个周期
    assert mind.pfc.consumed == [_SCOPE]
    assert any("实体推送" in step for step in ctx.execution_steps)

    # 一次性消费：下一轮不再重复注入
    await _merge_pushes(ctx, state)
    assert len(ctx.tool_chain) == 1


@pytest.mark.asyncio
async def test_merge_pushes_skips_snapshot_covered_entries() -> None:
    """base 快照前到达的推送已随短期记忆进入上下文，不再轮内注入。"""
    mind = FakeMind()
    hub = PushHub(mind)
    mind.push_hub = hub
    hub.push(_SCOPE, "devops", "构建完成", trigger=False)

    ctx = _make_ctx(mind, _SCOPE)
    state = SimpleNamespace(iteration=0, push_watermark=hub.current_seq(_SCOPE))
    await _merge_pushes(ctx, state)
    assert ctx.tool_chain == []
    # 但被丢弃的快照内条目不残留队列
    assert hub.drain_inflight(_SCOPE, since=0) == []


@pytest.mark.asyncio
async def test_merge_pushes_ignores_reflect_mode() -> None:
    mind = FakeMind()
    hub = PushHub(mind)
    mind.push_hub = hub
    hub.push(_SCOPE, "devops", "构建完成", trigger=False)

    ctx = _make_ctx(mind, _SCOPE)
    ctx.mode = ThinkMode.REFLECT
    state = SimpleNamespace(iteration=0, push_watermark=0)
    await _merge_pushes(ctx, state)
    assert ctx.tool_chain == []
