"""工具沉睡/激活状态机（agent.mind.tool_activation）单元测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.mind.tool_activation import (
    ToolActivationManager,
    bind_scope,
    reset_scope,
    tool_activation,
)


def test_core_tools_registered_once_at_bootstrap(tmp_path: Path) -> None:
    """导入并激活核心工具后，重复注册不丢工具或产生重复定义。"""
    result = subprocess.run(
        [sys.executable, "-c", """
from agent.mind.mind_tools import register_core_tools
from core.entity import EntityRegistry
register_core_tools()
first = EntityRegistry.get_tool_schema_by_names(['end_reply', 'compress_context'])
register_core_tools()
assert EntityRegistry.get_tool_schema_by_names(['end_reply', 'compress_context']) == first
assert {item['function']['name'] for item in first} == {'end_reply', 'compress_context'}
for name in ('end_reply', 'compress_context'):
    entity = EntityRegistry.get(name)
    assert entity.group == 'thinking' and entity.source == 'mind.core'
    assert 'always' in entity.tags
"""],
        capture_output=True, timeout=30,
        env=os.environ | {
            "ANELF_CONFIG_DIR": str(tmp_path / "config"),
            "ANELF_DATA_DIR": str(tmp_path / "data"),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_manual_compression_uses_bound_session(monkeypatch) -> None:
    """手动压缩沿用当前会话，其他会话的请求与焦点互不覆盖。"""
    from agent.context.compressor import ContextCompressor
    from agent.mind import mind_tools

    compressor = ContextCompressor(SimpleNamespace())
    monkeypatch.setattr(mind_tools, "compressor_port", SimpleNamespace(bound=True, get=lambda: compressor))
    for scope, focus in (("user_qq:1", "项目"), ("user_webui:1", "行程")):
        token = bind_scope(scope)
        try:
            assert json.loads(mind_tools._compress_context_tool(focus_topic=focus))["ok"]
        finally:
            reset_scope(token)
    assert compressor.pop_manual_focus("user_qq:1") == "项目"
    assert compressor.pop_manual_focus("user_webui:1") == "行程"
    assert compressor.pop_manual_focus("user_qq:1") is None


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

    def test_consume_round_expires(self, manager: ToolActivationManager, monkeypatch) -> None:
        # 默认粘性模式不过期；显式关闭粘性后按轮次过期
        monkeypatch.setattr(
            "core.config.get_config_bool",
            lambda k, d=False: False if k == "tool_activation_sticky" else d,
        )
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

    async def test_activate_existing_non_sleepable_group_hints_discovery(self) -> None:
        from agent.mind.tool_activation import _activate_tool_group_tool
        from core.entity import EntityRegistry

        EntityRegistry.register_tool(
            name="act_awake_t", func=lambda: "a", group="act_awakeg",
        )
        try:
            result = json.loads(_activate_tool_group_tool(group="act_awakeg"))
            assert "error" in result
            assert "list_entity_methods" in result["hint"]
            assert not tool_activation.is_active("act_awakeg")
        finally:
            EntityRegistry.unregister("act_awake_t")

    async def test_activate_sleepable_group(self) -> None:
        from agent.mind.tool_activation import _activate_tool_group_tool
        from core.entity import EntityRegistry

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


class TestDeactivateToolGroup:
    def test_deactivate_manager_roundtrip(self, manager: ToolActivationManager) -> None:
        manager.activate("code", rounds=3, scope="s1")
        assert manager.deactivate("code", "s1") is True
        assert not manager.is_active("code", "s1")
        assert manager.deactivate("code", "s1") is False  # 幂等：未激活返回 False
        # scope 容器清空后其余 scope 互不影响
        manager.activate("code", scope="s2")
        assert manager.is_active("code", "s2")

    async def test_deactivate_tool_not_active(self) -> None:
        from agent.mind.tool_activation import _deactivate_tool_group_tool
        token = bind_scope("scope_deact_test")
        try:
            result = json.loads(_deactivate_tool_group_tool(group="ghost_group"))
            assert "error" in result
            assert "active_groups" in result
        finally:
            reset_scope(token)

    async def test_deactivate_tool_roundtrip(self) -> None:
        from agent.mind.tool_activation import (
            _activate_tool_group_tool,
            _deactivate_tool_group_tool,
        )
        from core.entity import EntityRegistry

        EntityRegistry.register_tool(
            name="deact_sleepy", func=lambda: "s", group="deact_sleepg",
            allow_sleep=True, sleep_brief="简介",
        )
        try:
            token = bind_scope("scope_deact_rt")
            result = json.loads(_activate_tool_group_tool(group="deact_sleepg"))
            assert result["ok"] is True
            result = json.loads(_deactivate_tool_group_tool(group="deact_sleepg"))
            assert result["ok"] is True
            assert not tool_activation.is_active("deact_sleepg", "scope_deact_rt")
            reset_scope(token)
        finally:
            EntityRegistry.unregister("deact_sleepy")
            tool_activation.clear_scope("scope_deact_rt")
