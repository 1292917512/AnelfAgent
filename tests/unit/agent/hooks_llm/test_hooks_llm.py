"""LLM 钩子面（agent.hooks_llm）单元测试：注册表 / 快照 / 并行执行与治理。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from agent.hooks_llm import (
    HOOK_EVENTS,
    HookContext,
    HookContextMode,
    HookRegistry,
    llm_hook,
)
from agent.hooks_llm.executor import HookExecutor
from agent.hooks_llm.snapshot import (
    cap_snapshot_chars,
    freeze_messages,
    prepare_hook_messages,
)
from agent.hooks_llm.spec import LLMHookSpec


@pytest.fixture(autouse=True)
def _clean_registry():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


def _spec(name: str, event: str = "after_reply", **kw: Any) -> LLMHookSpec:
    async def _handler(ctx: HookContext) -> Optional[str]:
        return None
    return LLMHookSpec(name=name, event=event, handler=_handler, **kw)


# ==================================================================
# 注册表
# ==================================================================

def test_register_and_for_event_sorted_by_priority():
    HookRegistry.register(_spec("low", priority=10))
    HookRegistry.register(_spec("high", priority=90))
    HookRegistry.register(_spec("mid", priority=50))
    names = [s.name for s in HookRegistry.for_event("after_reply")]
    assert names == ["high", "mid", "low"]


def test_same_event_supports_multiple_hooks():
    HookRegistry.register(_spec("a"))
    HookRegistry.register(_spec("b"))
    assert len(HookRegistry.for_event("after_reply")) == 2


def test_register_same_name_overrides():
    HookRegistry.register(_spec("x", event="after_reply"))
    HookRegistry.register(_spec("x", event="delegation_resolved"))
    assert HookRegistry.get("x").event == "delegation_resolved"
    assert HookRegistry.for_event("after_reply") == []


def test_register_invalid_event_raises():
    with pytest.raises(ValueError):
        HookRegistry.register(_spec("bad", event="bogus_event"))


def test_unregister_and_owner():
    HookRegistry.register(_spec("a", owner="mod.x"))
    HookRegistry.register(_spec("b", owner="mod.x"))
    HookRegistry.register(_spec("c", owner="other"))
    removed = HookRegistry.unregister_by_owner("mod.x")
    assert removed == 2
    assert [s.name for s in HookRegistry.list_all()] == ["c"]


def test_llm_hook_decorator_registers():
    @llm_hook("deco", event="after_reply", context="transcript",
              tool_tags=["skills"], max_iterations=6, priority=70)
    async def _h(ctx: HookContext) -> Optional[str]:
        return None

    spec = HookRegistry.get("deco")
    assert spec is not None
    assert spec.context is HookContextMode.TRANSCRIPT
    assert spec.tool_tags == ("skills",)
    assert spec.priority == 70
    assert callable(spec.handler)


# ==================================================================
# 快照
# ==================================================================

def test_freeze_messages_copies_and_filters():
    original = [
        {"role": "user", "content": "a", "_layer": "stable"},
        "not-a-dict",  # type: ignore[list-item]
        {"role": "assistant", "content": "b"},
    ]
    frozen = freeze_messages(original)  # type: ignore[arg-type]
    assert len(frozen) == 2
    frozen[0]["content"] = "mutated"
    assert original[0]["content"] == "a"  # 原消息不受快照改写影响


def test_cap_snapshot_chars_truncates_with_marker():
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(100)]
    capped = cap_snapshot_chars(messages, max_chars=10_000)
    total = sum(len(m["content"]) for m in capped)
    assert total <= 10_000 + 200  # 省略标记自身字符另计（小）
    assert any("已省略" in str(m.get("content", "")) for m in capped)


def test_prepare_hook_messages_strips_internal_keys():
    messages = [
        {"role": "system", "content": "s", "_layer": "stable", "_source": {"origin": "x"}},
        {"role": "user", "content": "hi"},
    ]
    out = prepare_hook_messages(messages)
    assert all("_layer" not in m and "_source" not in m for m in out)
    assert out[0]["content"] == "s"


def test_prepare_hook_messages_empty():
    assert prepare_hook_messages(None) == []
    assert prepare_hook_messages([]) == []


# ==================================================================
# 并行执行与治理
# ==================================================================

def _make_executor() -> HookExecutor:
    mind = SimpleNamespace(background_tasks=None)
    return HookExecutor(mind, pool_size=2)


async def _drain(executor: HookExecutor, timeout: float = 2.0) -> None:
    """等待 executor 的后台钩子任务全部完成（dispatch 为后台调度，测试需显式等）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while executor._running and asyncio.get_event_loop().time() < deadline:
        await asyncio.gather(*list(executor._running), return_exceptions=True)
        await asyncio.sleep(0)


async def test_dispatch_runs_all_matched_hooks_in_parallel():
    ran: List[str] = []

    def _mk(name: str) -> LLMHookSpec:
        async def _h(ctx: HookContext) -> Optional[str]:
            await asyncio.sleep(0.01)
            ran.append(name)
            return None
        return LLMHookSpec(name=name, event="after_reply", handler=_h)

    specs = [_mk("h1"), _mk("h2"), _mk("h3")]
    executor = _make_executor()
    executor.dispatch("after_reply", specs, {"scope": "user_q:1"})
    await _drain(executor)
    assert sorted(ran) == ["h1", "h2", "h3"]


async def test_dispatch_respects_when_gate():
    ran: List[str] = []

    async def _h(ctx: HookContext) -> Optional[str]:
        ran.append(ctx.name)
        return None

    yes = LLMHookSpec(name="yes", event="after_reply", handler=_h,
                      when=lambda p: True)
    no = LLMHookSpec(name="no", event="after_reply", handler=_h,
                     when=lambda p: False)
    executor = _make_executor()
    executor.dispatch("after_reply", [yes, no], {})
    await _drain(executor)
    assert ran == ["yes"]


async def test_dispatch_skips_when_inside_hook_recursion_guard():
    from agent.hooks_llm.executor import _RecursionGuard
    ran: List[str] = []

    async def _h(ctx: HookContext) -> Optional[str]:
        ran.append(ctx.name)
        return None

    spec = LLMHookSpec(name="g", event="after_reply", handler=_h)
    executor = _make_executor()
    token = _RecursionGuard.enter()
    try:
        executor.dispatch("after_reply", [spec], {})
        await _drain(executor)
    finally:
        _RecursionGuard.exit(token)
    assert ran == []


async def test_transcript_disabled_drops_snapshot(monkeypatch):
    captured: Dict[str, Any] = {}

    async def _h(ctx: HookContext) -> Optional[str]:
        captured["messages"] = ctx.messages
        return None

    spec = LLMHookSpec(name="t", event="after_reply", handler=_h,
                       context=HookContextMode.TRANSCRIPT)
    executor = _make_executor()
    monkeypatch.setattr(HookExecutor, "_transcript_enabled", staticmethod(lambda: False))
    executor.dispatch("after_reply", [spec],
                      {"messages": [{"role": "user", "content": "x"}]})
    await _drain(executor)
    assert captured["messages"] == []


async def test_transcript_enabled_builds_snapshot(monkeypatch):
    captured: Dict[str, Any] = {}

    async def _h(ctx: HookContext) -> Optional[str]:
        captured["messages"] = ctx.messages
        return None

    spec = LLMHookSpec(name="t", event="after_reply", handler=_h,
                       context=HookContextMode.TRANSCRIPT)
    executor = _make_executor()
    monkeypatch.setattr(HookExecutor, "_transcript_enabled", staticmethod(lambda: True))
    executor.dispatch("after_reply", [spec],
                      {"messages": [{"role": "user", "content": "x"}]})
    await _drain(executor)
    assert len(captured["messages"]) == 1
    assert captured["messages"][0]["content"] == "x"


async def test_cooldown_skips_within_window():
    ran: List[str] = []

    async def _h(ctx: HookContext) -> Optional[str]:
        ran.append("x")
        return None

    spec = LLMHookSpec(name="c", event="after_reply", handler=_h,
                       cooldown_seconds=60.0)
    executor = _make_executor()
    executor.dispatch("after_reply", [spec], {"scope": "user_q:1"})
    executor.dispatch("after_reply", [spec], {"scope": "user_q:1"})
    await _drain(executor)
    assert ran == ["x"]  # 第二次在冷却期内被跳过


async def test_one_hook_failure_does_not_affect_others():
    ran: List[str] = []

    async def _boom(ctx: HookContext) -> Optional[str]:
        raise RuntimeError("boom")

    async def _ok(ctx: HookContext) -> Optional[str]:
        ran.append("ok")
        return None

    bad = LLMHookSpec(name="bad", event="after_reply", handler=_boom)
    good = LLMHookSpec(name="good", event="after_reply", handler=_ok)
    executor = _make_executor()
    # 后台 task 独立隔离：坏钩子异常不影响好钩子
    executor.dispatch("after_reply", [bad, good], {})
    await _drain(executor)
    assert ran == ["ok"]


# ==================================================================
# llm_end 事件（每次 LLM 调用后；高频护栏）
# ==================================================================

def test_llm_end_in_event_whitelist():
    assert "llm_end" in HOOK_EVENTS


def test_llm_end_hook_min_cooldown_clamped():
    from agent.hooks_llm.spec import LLM_END_MIN_COOLDOWN_SECONDS

    @llm_hook("llm_e", event="llm_end", context="transcript", cooldown_seconds=0)
    async def _h(ctx: HookContext) -> Optional[str]:
        return None

    spec = HookRegistry.get("llm_e")
    assert spec.cooldown_seconds == LLM_END_MIN_COOLDOWN_SECONDS


def test_non_llm_end_cooldown_not_clamped():
    @llm_hook("ar_e", event="after_reply", cooldown_seconds=0)
    async def _h(ctx: HookContext) -> Optional[str]:
        return None

    assert HookRegistry.get("ar_e").cooldown_seconds == 0.0


async def test_llm_end_transcript_uses_payload_messages(monkeypatch):
    """llm_end：transcript 快照取自 payload.messages（llm_invoker 附带的发送链）。"""
    captured: Dict[str, Any] = {}

    async def _h(ctx: HookContext) -> Optional[str]:
        captured["messages"] = ctx.messages
        captured["scope"] = ctx.scope
        return None

    spec = LLMHookSpec(name="le", event="llm_end", handler=_h,
                       context=HookContextMode.TRANSCRIPT)
    executor = _make_executor()
    monkeypatch.setattr(HookExecutor, "_transcript_enabled", staticmethod(lambda: True))
    payload = {
        "model": "m1", "duration_ms": 120, "tool_calls": ["recall"],
        "messages": [{"role": "user", "content": "本轮输入"}],
    }
    executor.dispatch("llm_end", [spec], payload)
    await _drain(executor)
    assert len(captured["messages"]) == 1
    assert captured["messages"][0]["content"] == "本轮输入"


async def test_llm_end_scope_fallback_to_contextvar(monkeypatch):
    """llm_end payload 无 scope 时从思维 ContextVar 推。"""
    captured: Dict[str, Any] = {}

    async def _h(ctx: HookContext) -> Optional[str]:
        captured["scope"] = ctx.scope
        return None

    spec = LLMHookSpec(name="le2", event="llm_end", handler=_h)
    executor = _make_executor()
    from agent.mind.tool_activation import bind_scope, reset_scope
    token = bind_scope("user_q:42")
    try:
        executor.dispatch("llm_end", [spec], {"model": "m"})
        await _drain(executor)
    finally:
        reset_scope(token)
    assert captured["scope"] == "user_q:42"


def test_llm_end_decorator_cooldown_independent_of_after_reply():
    """同事件的 llm_end 强制冷却不影响其他事件的冷却语义。"""
    from agent.hooks_llm.spec import LLM_END_MIN_COOLDOWN_SECONDS

    @llm_hook("x1", event="llm_end", cooldown_seconds=5)
    async def _a(ctx: HookContext) -> Optional[str]:
        return None

    @llm_hook("x2", event="llm_end", cooldown_seconds=60)
    async def _b(ctx: HookContext) -> Optional[str]:
        return None

    assert HookRegistry.get("x1").cooldown_seconds == LLM_END_MIN_COOLDOWN_SECONDS  # 5→钳到 20
    assert HookRegistry.get("x2").cooldown_seconds == 60.0  # 60 保留


async def test_debounce_merges_window_triggers_to_latest():
    """防抖：窗口内密集触发严格合并为一次（取最后快照）。"""
    ran: List[Any] = []

    async def _h(ctx: HookContext) -> Optional[str]:
        ran.append(ctx.payload.get("seq"))
        return None

    spec = LLMHookSpec(name="db", event="after_reply", handler=_h,
                       debounce_seconds=0.08)
    executor = _make_executor()
    for i in range(1, 6):
        executor.dispatch("after_reply", [spec], {"scope": "s", "seq": i})
        await asyncio.sleep(0.03)
    await asyncio.sleep(0.3)
    assert ran == [5]


async def test_dispatch_is_non_blocking():
    """dispatch 后台调度立即返回，不阻塞 emit 方到钩子执行完成。"""
    import time as _time

    async def _slow(ctx: HookContext) -> Optional[str]:
        await asyncio.sleep(0.2)
        return None

    spec = LLMHookSpec(name="slow", event="after_reply", handler=_slow)
    executor = _make_executor()
    t0 = _time.monotonic()
    executor.dispatch("after_reply", [spec], {})
    assert _time.monotonic() - t0 < 0.05  # 立即返回，不等 0.2s 的钩子
    await _drain(executor)


async def test_cancel_pending_stops_debounced_not_running():
    """cancel_pending 只停未开始的防抖任务，不取消已开始执行的在途任务（drain 才等它们）。"""
    started = asyncio.Event()

    async def _h(ctx: HookContext) -> Optional[str]:
        started.set()
        await asyncio.sleep(5)
        return None

    running = LLMHookSpec(name="run", event="after_reply", handler=_h)
    debounced = LLMHookSpec(name="deb", event="after_reply", handler=_h,
                            debounce_seconds=5.0)
    executor = _make_executor()
    executor.dispatch("after_reply", [running], {})
    executor.dispatch("after_reply", [debounced], {})
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(executor._running) == 1          # run 在途
    assert len(executor._debounce_handle) == 1  # deb 防抖待发
    executor.cancel_pending()
    assert len(executor._debounce_handle) == 0  # 防抖待发被取消
    assert len(executor._running) == 1          # 在途任务不被 cancel_pending 取消
    await executor.drain(timeout=0.2)           # drain 才负责超时取消在途
    assert len(executor._running) == 0


# ==================================================================
# 冷却锚点时机 / 关停 drain 语义
# ==================================================================

async def test_cooldown_not_leaked_by_back_to_back_dispatch():
    """连续同步触发同一冷却钩子不漏冷却（锚点在调度受理时同步记录）。

    第一个后台 task 尚未及执行时第二次 dispatch 已到达——锚点必须在调度
    受理时刻同步记录，否则第二次查 _last_fired 为空而漏判冷却重复受理。
    """
    ran: List[int] = []

    async def _h(ctx: HookContext) -> Optional[str]:
        ran.append(1)
        return None

    spec = LLMHookSpec(name="c", event="after_reply", handler=_h,
                       cooldown_seconds=60.0)
    executor = _make_executor()
    executor.dispatch("after_reply", [spec], {"scope": "s"})
    executor.dispatch("after_reply", [spec], {"scope": "s"})
    await _drain(executor)
    assert ran == [1]


async def test_drain_waits_running_cancels_debounced():
    """drain 等待运行中的钩子收尾，取消防抖待发（未开始的）。"""
    finished: List[str] = []

    async def _slow(ctx: HookContext) -> Optional[str]:
        await asyncio.sleep(0.2)
        finished.append(ctx.name)
        return "done"

    running = LLMHookSpec(name="review", event="after_reply", handler=_slow)
    debounced = LLMHookSpec(name="deb", event="after_reply", handler=_slow,
                            debounce_seconds=5.0)
    executor = HookExecutor(SimpleNamespace(background_tasks=None), pool_size=2)
    executor.dispatch("after_reply", [running], {})
    executor.dispatch("after_reply", [debounced], {})
    await asyncio.sleep(0.05)  # review 已开始，deb 仍在防抖窗口
    await executor.drain(timeout=5.0)
    assert finished == ["review"]  # 运行中的被执行完；防抖待发被取消


async def test_drain_timeout_cancels_overrun():
    """drain 超时后取消未完成的在途任务（不无限等待）。"""
    started = asyncio.Event()

    async def _hang(ctx: HookContext) -> Optional[str]:
        started.set()
        await asyncio.sleep(30)
        return None

    spec = LLMHookSpec(name="hang", event="after_reply", handler=_hang)
    executor = HookExecutor(SimpleNamespace(background_tasks=None), pool_size=1)
    executor.dispatch("after_reply", [spec], {})
    await asyncio.wait_for(started.wait(), timeout=1.0)
    import time as _time
    t0 = _time.monotonic()
    await executor.drain(timeout=0.2)
    assert _time.monotonic() - t0 < 1.0  # 超时即取消，不等满 30s
