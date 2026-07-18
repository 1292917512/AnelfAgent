"""工具门控（core.tool_gate + EntityRegistry 门控过滤）单元测试。"""

from __future__ import annotations

import pytest

from core.entity import EntityRegistry, EntityType
from core.tool_gate import ToolGate


@pytest.fixture
def gate() -> ToolGate:
    return ToolGate(ttl_seconds=30.0, failure_grace_seconds=60.0)


@pytest.fixture
def registered_tools():
    """注册测试工具并在用例后清理。"""
    names = ["gate_t1", "gate_t2", "gate_sleepy"]
    yield names
    for n in names:
        EntityRegistry.unregister(n)


class TestToolGate:
    async def test_check_pass_and_cache(self, gate: ToolGate) -> None:
        calls = {"n": 0}

        def check() -> bool:
            calls["n"] += 1
            return True

        assert await gate.check(check) is True
        assert await gate.check(check) is True
        assert calls["n"] == 1, "TTL 内不应重复探测"

    async def test_check_failure_cached(self, gate: ToolGate) -> None:
        calls = {"n": 0}

        def check() -> bool:
            calls["n"] += 1
            return False

        assert await gate.check(check) is False
        assert await gate.check(check) is False
        assert calls["n"] == 1

    async def test_transient_failure_grace(self, gate: ToolGate) -> None:
        state = {"ok": True}

        def check() -> bool:
            return state["ok"]

        assert await gate.check(check) is True
        gate.invalidate()
        state["ok"] = False
        # 宽限期内：返回 last-good True，且失败不缓存
        assert await gate.check(check) is True
        # 宽限期过后（模拟 last_good 过期）→ 真实失败
        gate._last_good[check] = 0.0
        gate.invalidate()
        assert await gate.check(check) is False

    async def test_async_check_fn(self, gate: ToolGate) -> None:
        async def check() -> bool:
            return True

        assert await gate.check(check) is True

    async def test_check_exception_treated_as_failure(self, gate: ToolGate) -> None:
        def check() -> bool:
            raise RuntimeError("boom")

        assert await gate.check(check) is False

    async def test_none_check_fn_passes(self, gate: ToolGate) -> None:
        assert await gate.check(None) is True

    async def test_filter_names_dedupes_fn(self, gate: ToolGate) -> None:
        calls = {"n": 0}

        def check() -> bool:
            calls["n"] += 1
            return True

        result = await gate.filter_names({"a": check, "b": check, "c": None})
        assert result == {"a": True, "b": True, "c": True}
        assert calls["n"] == 1, "同一 check_fn 一次评估只探测一次"


class TestRegistryGating:
    async def test_get_active_tools_filters(self, registered_tools) -> None:
        state = {"ok": False}
        EntityRegistry.register_tool(
            name="gate_t1", func=lambda: "1", group="gate_test",
            check_fn=lambda: state["ok"],
        )
        EntityRegistry.register_tool(name="gate_t2", func=lambda: "2", group="gate_test")

        active = await EntityRegistry.get_active_tools(["gate_t1", "gate_t2"])
        assert [e.name for e in active] == ["gate_t2"]

        state["ok"] = True
        from core.tool_gate import tool_gate
        tool_gate.invalidate()
        active = await EntityRegistry.get_active_tools(["gate_t1", "gate_t2"])
        assert {e.name for e in active} == {"gate_t1", "gate_t2"}

    async def test_get_active_tools_ignores_unknown(self, registered_tools) -> None:
        active = await EntityRegistry.get_active_tools(["nonexistent_tool"])
        assert active == []

    def test_sleepable_groups(self, registered_tools) -> None:
        EntityRegistry.register_tool(
            name="gate_sleepy", func=lambda: "s", group="gate_sleepg",
            allow_sleep=True, sleep_brief="沉睡简介",
        )
        sleepable = EntityRegistry.get_sleepable_groups()
        assert "gate_sleepg" in sleepable
        assert sleepable["gate_sleepg"]["brief"] == "沉睡简介"
        assert sleepable["gate_sleepg"]["tool_count"] == 1

    def test_register_tool_meta_properties(self, registered_tools) -> None:
        EntityRegistry.register_tool(
            name="gate_t1", func=lambda: "1", group="gate_test",
            check_fn=lambda: True, allow_sleep=True, sleep_brief="b",
        )
        e = EntityRegistry.get("gate_t1")
        assert e is not None and e.entity_type == EntityType.TOOL
        assert callable(e.check_fn)
        assert e.allow_sleep is True
        assert e.sleep_brief == "b"
