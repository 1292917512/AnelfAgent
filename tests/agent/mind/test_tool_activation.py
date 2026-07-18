"""工具沉睡/激活状态机（agent.mind.tool_activation）单元测试。"""

from __future__ import annotations

import json

import pytest

from agent.mind.tool_activation import (
    ToolActivationManager,
    bind_scope,
    reset_scope,
    tool_activation,
)


@pytest.fixture
def manager() -> ToolActivationManager:
    return ToolActivationManager()


class TestToolActivationManager:
    def test_activate_and_rounds(self, manager: ToolActivationManager) -> None:
        rounds = manager.activate("web", rounds=5, scope="s1")
        assert rounds == 5
        assert manager.is_active("web", "s1")
        assert manager.rounds_left("web", "s1") == 5

    def test_default_rounds(self, manager: ToolActivationManager) -> None:
        rounds = manager.activate("web", scope="s1")
        assert rounds >= 1

    def test_max_rounds_clamped(self, manager: ToolActivationManager) -> None:
        rounds = manager.activate("web", rounds=999, scope="s1")
        assert rounds <= 20

    def test_consume_round_expires(self, manager: ToolActivationManager) -> None:
        manager.activate("web", rounds=2, scope="s1")
        assert manager.consume_round("s1") == []
        assert manager.is_active("web", "s1")
        assert manager.consume_round("s1") == ["web"]
        assert not manager.is_active("web", "s1")

    def test_extend(self, manager: ToolActivationManager) -> None:
        manager.activate("web", rounds=2, scope="s1")
        assert manager.extend("web", rounds=3, scope="s1") == 5

    def test_scope_isolation(self, manager: ToolActivationManager) -> None:
        manager.activate("web", rounds=3, scope="s1")
        assert not manager.is_active("web", "s2")
        assert manager.active_groups("s2") == {}

    def test_clear_scope(self, manager: ToolActivationManager) -> None:
        manager.activate("web", rounds=3, scope="s1")
        manager.clear_scope("s1")
        assert not manager.is_active("web", "s1")


class TestScopeBinding:
    def test_bind_and_reset(self) -> None:
        token = bind_scope("user_1")
        assert ToolActivationManager.current_scope() == "user_1"
        reset_scope(token)
        assert ToolActivationManager.current_scope() == "_global"

    def test_activate_uses_bound_scope(self) -> None:
        token = bind_scope("user_bind")
        try:
            tool_activation.activate("web", rounds=1)
            assert tool_activation.is_active("web", "user_bind")
        finally:
            reset_scope(token)
            tool_activation.clear_scope("user_bind")


class TestActivateToolGroupTool:
    async def test_activate_unknown_group(self) -> None:
        from agent.mind.tool_activation import _activate_tool_group_tool
        result = json.loads(_activate_tool_group_tool(group="no_such_group_xyz"))
        assert "error" in result

    async def test_activate_sleepable_group(self) -> None:
        from core.entity import EntityRegistry
        from agent.mind.tool_activation import _activate_tool_group_tool

        EntityRegistry.register_tool(
            name="act_sleepy", func=lambda: "s", group="act_sleepg",
            allow_sleep=True, sleep_brief="简介",
        )
        try:
            token = bind_scope("scope_tool_test")
            result = json.loads(_activate_tool_group_tool(group="act_sleepg", rounds=2))
            assert result["ok"] is True
            assert result["active_rounds"] == 2
            assert any(t["name"] == "act_sleepy" for t in result["tools"])
            assert tool_activation.is_active("act_sleepg", "scope_tool_test")
            reset_scope(token)
        finally:
            EntityRegistry.unregister("act_sleepy")
            tool_activation.clear_scope("scope_tool_test")
