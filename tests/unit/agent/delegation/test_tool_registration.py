"""委托工具组的声明、激活与独立工厂装配。"""

from __future__ import annotations

import os
import subprocess
import sys

EXPECTED_TOOLS = {
    "delegate_task",
    "send_to_agent",
    "follow_up_agent",
}


def _schema_names() -> set:
    from core.entity import EntityRegistry
    return {
        str(s.get("function", {}).get("name", ""))
        for s in EntityRegistry.get_tool_schemas_by_group("delegation")
    }


class TestDelegationGroupRegistration:
    def test_activation_registers_all_tools(self) -> None:
        """机制层：delegate_tool 已导入时 activate_group 注册全部委托工具。

        共享测试会话中组可能已被先前用例激活（弹出后注册表为空），
        两种情况都断言注册表终态完整。
        """
        import agent.delegation.delegate_tool  # noqa: F401
        from core.tool_registry import _deferred_registry, activate_group

        if "delegation" in _deferred_registry:
            count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
            assert count == len(EXPECTED_TOOLS)

        from core.entity import EntityRegistry
        assert EntityRegistry.get_group_description("delegation") != ""
        assert EXPECTED_TOOLS <= _schema_names()

    def test_factory_registers_tools_without_bootstrap_imports(self, tmp_path) -> None:
        """独立进程只调用工厂，也必须取得完整工具目录。"""
        result = subprocess.run(
            [sys.executable, "-c", """
import asyncio
from unittest.mock import DEFAULT, MagicMock, patch
from agent.runtime import mind_factory
from core.entity import EntityRegistry

async def check():
    with patch.multiple(mind_factory,
        get_skill_store=DEFAULT, get_plugin_manager=DEFAULT, get_embedder=DEFAULT,
        SkillMatcher=DEFAULT, SkillCurator=DEFAULT, SkillReviewer=DEFAULT,
        HeartbeatEngine=DEFAULT, ContextCompressor=DEFAULT,
    ), patch('agent.delegation.delegation_manager.DelegationManager'):
        mind = mind_factory.create_mind(
            char=MagicMock(), llm=MagicMock(), llm_manager=None,
            channel_manager=MagicMock(), everything_data=MagicMock(),
            conversation_data=MagicMock(), memory_store=None,
        )
    try:
        expected = {'delegate_task', 'send_to_agent', 'follow_up_agent'}
        assert expected <= set(EntityRegistry.get_all_names())
        assert EntityRegistry.get_group_description('delegation')
        for name in ('end_reply', 'schedule_reply', 'switch_session', 'judge', 'check_background_tasks'):
            assert EntityRegistry.get(name) is not None, name
        assert mind.push_hub and mind.background_tasks and mind.model_invoker
    finally:
        await mind.background_tasks.close()
        await mind.push_hub.close()

asyncio.run(check())
"""],
            capture_output=True, timeout=30,
            env=os.environ | {
                "ANELF_CONFIG_DIR": str(tmp_path / "config"),
                "ANELF_DATA_DIR": str(tmp_path / "data"),
                "PYTHONIOENCODING": "utf-8",
            },
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
