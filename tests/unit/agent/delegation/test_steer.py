"""子代理转向（steer：步骤边界注入，agent.delegation.steer）单元测试。"""

from __future__ import annotations

import asyncio

from agent.delegation.steer import (
    SteerInbox,
    bind_steer_drain,
    drain_steered_messages,
    steer_inbox,
)
from agent.delegation.sub_agent import SubAgent


class TestSteerInbox:
    def test_push_drain_roundtrip(self) -> None:
        inbox = SteerInbox()
        assert inbox.push("d1", "改用 Python 实现") is True
        assert inbox.pending_count("d1") == 1
        assert inbox.drain("d1") == ["改用 Python 实现"]
        # 一次性取走：再 drain 为空
        assert inbox.drain("d1") == []
        assert inbox.pending_count("d1") == 0

    def test_empty_and_invalid_rejected(self) -> None:
        inbox = SteerInbox()
        assert inbox.push("", "msg") is False
        assert inbox.push("d1", "  ") is False

    def test_cap_per_delegation(self) -> None:
        inbox = SteerInbox()
        for i in range(8):
            assert inbox.push("d1", f"m{i}") is True
        assert inbox.push("d1", "第9条") is False
        assert inbox.pending_count("d1") == 8
        # 上限按委托隔离
        assert inbox.push("d2", "另一委托") is True

    def test_message_truncated(self) -> None:
        inbox = SteerInbox()
        inbox.push("d1", "x" * 5000)
        drained = inbox.drain("d1")
        assert len(drained) == 1
        assert len(drained[0]) < 5000
        assert drained[0].endswith("…(截断)")

    def test_clear(self) -> None:
        inbox = SteerInbox()
        inbox.push("d1", "m")
        inbox.clear("d1")
        assert inbox.drain("d1") == []


class TestDrainHook:
    def test_unbound_returns_empty(self) -> None:
        assert drain_steered_messages() == []

    def test_bound_drain_visible_and_reset(self) -> None:
        inbox = SteerInbox()
        inbox.push("d1", "转向A")
        with bind_steer_drain(lambda: inbox.drain("d1")):
            assert drain_steered_messages() == ["转向A"]
            assert drain_steered_messages() == []  # 已取走
        # 退出绑定后回空（ContextVar reset）
        assert drain_steered_messages() == []

    async def test_hook_propagates_into_created_task(self) -> None:
        """ContextVar 经 create_task 复制：子任务内 drain 可见（SubAgent 场景）。"""
        inbox = SteerInbox()
        inbox.push("d9", "传播检查")
        seen: list = []

        async def child() -> None:
            seen.extend(drain_steered_messages())

        with bind_steer_drain(lambda: inbox.drain("d9")):
            task = asyncio.create_task(child())
        await task
        assert seen == ["传播检查"]


class TestMergeSteered:
    def test_injects_into_tool_chain(self) -> None:
        from types import SimpleNamespace

        from agent.mind.tools.round_helpers import _merge_steered_messages

        ctx = SimpleNamespace(tool_chain=[], execution_steps=[])
        state = SimpleNamespace(iteration=3)
        with bind_steer_drain(lambda: ["需求变更：只保留中文输出"]):
            _merge_steered_messages(ctx, state)
        assert len(ctx.tool_chain) == 1
        msg = ctx.tool_chain[0]
        assert msg["role"] == "user"
        assert "[转向指令]" in msg["content"]
        assert "只保留中文输出" in msg["content"]
        assert msg["_source"] == {"origin": "steer"}
        assert any("转向" in s for s in ctx.execution_steps)

    def test_no_messages_noop(self) -> None:
        from types import SimpleNamespace

        from agent.mind.tools.round_helpers import _merge_steered_messages

        ctx = SimpleNamespace(tool_chain=[], execution_steps=[])
        _merge_steered_messages(ctx, SimpleNamespace(iteration=0))
        assert ctx.tool_chain == []


class TestManagerSteer:
    def _manager(self) -> "object":
        from agent.delegation.delegation_manager import DelegationManager

        manager = DelegationManager.__new__(DelegationManager)
        manager._mind = None
        manager._running = {"d1": {"goal": "生成图表", "scope": "user_qq:1"}}
        return manager

    def test_running_delegation_accepts(self) -> None:
        manager = self._manager()
        result = manager.steer("d1", "改成折线图")
        assert result.get("ok") is True
        assert steer_inbox.pending_count("d1") == 1
        steer_inbox.clear("d1")

    def test_unknown_delegation_rejected(self) -> None:
        manager = self._manager()
        result = manager.steer("ghost", "msg")
        assert "error" in result
        assert "check_background_tasks" in result["hint"]

    def test_empty_message_rejected(self) -> None:
        manager = self._manager()
        assert "error" in manager.steer("d1", "  ")


class TestSubAgentBinding:
    async def test_run_drains_and_clears_inbox(self) -> None:
        """SubAgent 运行中 drain 闭包生效；结束后清箱。"""

        class _FakeMind:
            async def reflect(self, messages, **kwargs) -> str:
                # 执行期间（think_loop 语义位置）取走转向消息
                drained.append(drain_steered_messages())
                return "完成"

        drained: list = []
        steer_inbox.push("dz", "中途补充：输出加单位")
        agent = SubAgent(_FakeMind(), "计算增长率", delegation_id="dz")
        await asyncio.wait_for(agent.run(), timeout=5)

        assert drained == [["中途补充：输出加单位"]]
        # 结束清箱
        assert steer_inbox.pending_count("dz") == 0

    async def test_run_without_id_never_drains(self) -> None:
        class _FakeMind:
            async def reflect(self, messages, **kwargs) -> str:
                drained.append(drain_steered_messages())
                return "完成"

        drained: list = []
        steer_inbox.push("other", "别的委托的消息")
        agent = SubAgent(_FakeMind(), "任务")
        await asyncio.wait_for(agent.run(), timeout=5)
        assert drained == [[]]
        steer_inbox.clear("other")


class TestFrontDelegateRegistry:
    """前台委托注册表集成：check 可见、claimed 收尾不双投递、terminate 可停。"""

    def _registry_mind(self, output: str = "前台结果", delay: float = 0.0):
        from agent.mind.background_tasks import BackgroundTaskRegistry

        class _Mind:
            def __init__(self) -> None:
                self.background_tasks = BackgroundTaskRegistry()
                self.unclaimed: list = []
                self.background_tasks.set_unclaimed_callback(
                    lambda scope, desc, summary: self.unclaimed.append((scope, desc)),
                )

            async def reflect(self, messages, **kwargs) -> str:
                if delay:
                    await asyncio.sleep(delay)
                return output

            def get_model_context_length(self) -> int:
                return 128_000

        return _Mind()

    async def test_registers_and_completes_claimed(self) -> None:
        from agent.delegation.delegation_manager import DelegationManager

        mind = self._registry_mind()
        manager = DelegationManager(mind)
        result = await asyncio.wait_for(
            manager.delegate("前台调研", scope_hint="user_qq:front"), timeout=5,
        )
        assert result.success is True
        assert result.completed_reason == "completed"
        # 完成后条目已收尾，且未触发轮外完成回调（工具结果即通知本体）
        assert mind.unclaimed == []
        assert mind.background_tasks.running("user_qq:front") == []

    async def test_terminate_front_delegate(self) -> None:
        from agent.delegation.delegation_manager import DelegationManager

        mind = self._registry_mind(output="不应到达", delay=2.0)
        manager = DelegationManager(mind)

        async def _run() -> None:
            result = await manager.delegate("慢任务", scope_hint="user_qq:slow")
            results.append(result)

        results: list = []
        task = asyncio.create_task(_run())
        await asyncio.sleep(0.1)
        running = mind.background_tasks.running("user_qq:slow")
        assert len(running) == 1  # 前台委托运行中可见
        task_id = running[0].task_id
        killed = mind.background_tasks.terminate("user_qq:slow", task_id)
        assert killed.get("ok") is True
        await asyncio.wait_for(task, timeout=5)
        assert results[0].cancelled is True
