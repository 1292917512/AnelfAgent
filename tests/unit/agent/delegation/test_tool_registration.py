"""delegation 工具组生产注册链路回归测试。

回归自 2026-09 潜伏缺陷：`activate_group("delegation")` 在 Mind 构造期执行，
但全仓唯一 import delegate_tool（填充 deferred 注册表）的是 wire_runtime
函数体——在 Mind 构造之后才运行，导致 activate 弹出空组、delegation 整组
从未进入 EntityRegistry（组名都不登记），delegate_task 生产环境完全不可用。
单测因直接 import delegate_tool 一直绿，未暴露该时序缺陷。
"""

from __future__ import annotations

from pathlib import Path

EXPECTED_TOOLS = {
    "delegate_task",
    "check_background_tasks",
    "terminate_background_task",
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
        """机制层：delegate_tool 已导入时 activate_group 注册全部 5 个工具。

        共享测试会话中组可能已被先前用例激活（弹出后注册表为空），
        两种情况都断言注册表终态完整。
        """
        import agent.delegation.delegate_tool  # noqa: F401
        from entities._sdk import _deferred_registry, activate_group

        if "delegation" in _deferred_registry:
            count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
            assert count == len(EXPECTED_TOOLS)

        from core.entity import EntityRegistry
        assert EntityRegistry.get_group_description("delegation") != ""
        assert EXPECTED_TOOLS <= _schema_names()

    def test_bootstrap_preimport_guarantee(self) -> None:
        """时序守卫：assemble_runtime 的提前导入清单必须覆盖 delegate_tool。

        Mind 构造期激活 thinking/session/delegation 组，其 deferred 模块须先
        导入——该保障位于 bootstrap assemble_runtime（thinking/session 同例），
        被移除时本测试即红（防止时序缺陷静默回归）。
        """
        source = Path("agent/runtime/bootstrap.py").read_text(encoding="utf-8")
        assert "import agent.delegation.delegate_tool" in source
