"""反思产出语义与结束原因（think_loop REFLECT 细化）单元测试。

1. 产出语义：模型发起工具调用即把此前的纯文本判为中间独白（归档过程），
   产出只保留收束前最后一个未被工具调用打断的连续文本段；
2. 结束原因：completed / budget_exhausted / interrupted 随 completion 容器写出。
"""

from __future__ import annotations

from helpers.think_loop_fakes import (
    FakeMind,
    FakePfc,
    run_think_loop,
    text_result,
    tool_result,
)

from agent.mind.tools.think_loop import ThinkMode


def _reflect_mind(rounds: list) -> FakeMind:
    return FakeMind(rounds=rounds, default_text=None, pfc=FakePfc(exec_layer=True))


def _base() -> list:
    """每用例独立的基准消息（防跨测试共享列表被循环过程修改）。"""
    return [{"role": "user", "content": "分析一下"}]


class TestReflectOutputSemantics:
    async def test_interim_text_dropped_on_tool_round(self) -> None:
        """文本 → 工具 → 文本：中间独白被归档，产出只有最终总结段。"""
        # 末段连续 3 轮纯文本触发 REFLECT 收束（连续文本上限）
        mind = _reflect_mind([
            text_result("我先分析一下数据来源……"),
            tool_result("", ["recall"]),
            text_result("结论：数据来自 A 与 B 两处。"),
            text_result("结论：数据来自 A 与 B 两处。"),
            text_result("结论：数据来自 A 与 B 两处。"),
        ])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected,
        )
        # 中间独白被清空，产出只含收束前的连续最终段（3 轮全保留）
        assert collected and all(c == "结论：数据来自 A 与 B 两处。" for c in collected)
        assert "我先分析" not in "".join(collected)

    async def test_consecutive_text_rounds_kept(self) -> None:
        """连续文本轮（未被工具打断）整段保留为产出。"""
        mind = _reflect_mind([
            text_result("第一部分结论。"),
            text_result("第二部分结论。"),
            text_result("第二部分结论。"),
        ])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected,
        )
        # 连续文本段整段保留（未被工具打断）
        assert collected[0] == "第一部分结论。"
        assert "第二部分结论。" in collected

    async def test_interim_text_logged_to_steps(self) -> None:
        """被丢弃的中间独白在 execution_steps 留痕（过程可追溯）。"""
        mind = _reflect_mind([
            text_result("中间独白内容"),
            tool_result("", ["recall"]),
            text_result("最终结论"),
            text_result("最终结论"),
            text_result("最终结论"),
        ])
        steps: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            steps=steps,
        )
        assert any("中间独白" in s for s in steps)


class TestCompletionReason:
    async def test_normal_completion(self) -> None:
        mind = _reflect_mind([text_result("完成") for _ in range(3)])
        completion: dict = {}
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            completion=completion,
        )
        assert completion["reason"] == "completed"

    async def test_budget_exhausted(self) -> None:
        """轮次预算用尽：产出可能是中途状态，原因标记为 budget_exhausted。"""
        # 每轮都调工具 → 永不收敛 → 3 轮预算耗尽
        rounds = [tool_result("", ["recall"]) for _ in range(5)]
        mind = _reflect_mind(rounds)
        completion: dict = {}
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            safety_limit=3, completion=completion,
        )
        assert completion["reason"] == "budget_exhausted"

    async def test_no_completion_container_is_noop(self) -> None:
        """不传 completion 容器：行为不变（其他调用方零影响）。"""
        mind = _reflect_mind([text_result("完成") for _ in range(3)])
        await run_think_loop(mind, mode=ThinkMode.REFLECT, base_messages=_base())


class TestSubAgentCompletion:
    async def test_subagent_carries_reason(self) -> None:
        """SubAgent 把 reflect 的结束原因写进 SubAgentResult。"""
        from agent.delegation.sub_agent import SubAgent

        class _BudgetMind:
            async def reflect(self, messages, **kwargs) -> str:
                kwargs["completion"]["reason"] = "budget_exhausted"
                return "中途状态文本"

        agent = SubAgent(_BudgetMind(), "任务")
        result = await agent.run()
        assert result.success is True
        assert result.completed_reason == "budget_exhausted"
        assert result.to_dict()["completed_reason"] == "budget_exhausted"

    async def test_subagent_no_output_reason(self) -> None:
        from agent.delegation.sub_agent import SubAgent

        class _EmptyMind:
            async def reflect(self, messages, **kwargs) -> str:
                return ""

        result = await SubAgent(_EmptyMind(), "任务").run()
        assert result.success is False
        assert result.completed_reason == "no_output"


class TestAggregateReason:
    def _manager(self):
        from types import SimpleNamespace

        from agent.delegation.delegation_manager import DelegationManager

        manager = DelegationManager.__new__(DelegationManager)
        manager._mind = SimpleNamespace(get_model_context_length=lambda: 128_000)
        return manager

    def test_budget_exhausted_hint_in_aggregate(self) -> None:
        import json

        from agent.delegation.sub_agent import SubAgentResult

        manager = self._manager()
        out = json.loads(manager.aggregate_results([
            SubAgentResult(goal="调研", success=True, output="部分结论",
                           completed_reason="budget_exhausted"),
        ]))
        item = out["results"][0]
        assert item["completed_reason"] == "budget_exhausted"
        assert "拆分" in item["hint"]

    def test_completed_items_unchanged(self) -> None:
        import json

        from agent.delegation.sub_agent import SubAgentResult

        manager = self._manager()
        out = json.loads(manager.aggregate_results([
            SubAgentResult(goal="调研", success=True, output="结论"),
        ]))
        assert "completed_reason" not in out["results"][0]
