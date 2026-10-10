"""反思产出语义与结束原因（think_loop REFLECT 细化）单元测试。

1. 产出语义：模型发起工作工具调用即把此前的纯文本判为中间独白（归档过程），
   产出只保留收束前最后一个未被工具调用打断的连续文本段；
   end_reply 是收束信号而非工作工具——纯 end_reply 批次不清空已收集结论，
   其同批正文即最终连续文本段，纳入产出；
2. 结束原因：completed / budget_exhausted / interrupted 随 completion 容器写出。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeMind,
    FakePfc,
    end_reply_result,
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


class TestEndReplyOutputPreservation:
    """end_reply 是收束信号而非工作工具：其使用不摧毁已收集产出。

    回归自 2026-09 子代理 no_output 事故：reflect 契约教模型"完成就调
    end_reply"，但纯文本轮后的裸 end_reply 会触发独白清除、同批正文又
    从不进 collected_text——照契约办事必丢产出（子代理/任务/内省同病）。
    """

    async def test_bare_end_reply_preserves_conclusion(self) -> None:
        """结论文本轮 → 裸 end_reply 收束：结论保留（纯批次不构成"工作打断"）。"""
        mind = _reflect_mind([
            text_result("最终答案：543。"),
            end_reply_result(),
        ])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected,
        )
        assert collected == ["最终答案：543。"]

    async def test_end_reply_same_round_text_collected(self) -> None:
        """结论与 end_reply 同轮发表：同批文本即最终连续文本段，纳入产出。"""
        mind = _reflect_mind([
            tool_result("最终答案：543。", ["end_reply"]),
        ])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected,
        )
        assert collected == ["最终答案：543。"]

    async def test_conclusion_segments_joined_across_end_reply(self) -> None:
        """连续结论段 + end_reply 同批文本：无工作打断的整段全部保留。"""
        mind = _reflect_mind([
            text_result("第一部分。"),
            tool_result("第二部分。", ["end_reply"]),
        ])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected,
        )
        assert collected == ["第一部分。", "第二部分。"]

    async def test_mixed_batch_still_drops_interim_text(self) -> None:
        """混合批次（工作工具 + end_reply）：此前独白仍归档，同批结论文本保留。"""
        mind = _reflect_mind([
            text_result("我先查一下……"),
            tool_result("最终答案：543。", ["recall", "end_reply"]),
        ])
        collected: list = []
        steps: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, base_messages=_base(),
            collected_text=collected, steps=steps,
        )
        assert collected == ["最终答案：543。"]
        assert any("中间独白" in s for s in steps)


class TestRequiredOutput:
    async def test_empty_end_recovers_summary_from_existing_results(self) -> None:
        summary = '{"summary":"已核验木镐增加一把"}'
        mind = _reflect_mind([
            tool_result("", ["craft_item"]), end_reply_result(), text_result(summary),
        ])
        collected: list = []
        chain: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, require_output=True,
            collected_text=collected, chain=chain,
        )
        assert collected == [summary]
        assert mind.executed_tools.count("craft_item") == 1
        assert mind.llm_calls == 3
        assert any(m.get("_source", {}).get("origin") == "output_recovery" for m in chain)

    async def test_recovery_blocks_streaming_and_batch_tools(self, monkeypatch) -> None:
        from agent.mind.tools.think_loop import _EarlyToolRunner

        summary = "已有操作结果已核验，未再操作。"
        mind = _reflect_mind([
            end_reply_result(), tool_result(summary, ["get_inventory", "craft_item", "end_reply"]),
        ])
        original_invoke = mind._invoke_llm_unified

        async def invoke(*args, **kwargs):
            result = await original_invoke(*args, **kwargs)
            callback = kwargs.get("on_tool_call_ready")
            if mind.llm_calls == 2 and callback is not None:
                for tc in result.tool_calls:
                    callback(tc)
            return result

        monkeypatch.setattr(mind, "_invoke_llm_unified", invoke)
        monkeypatch.setattr(_EarlyToolRunner, "_is_readonly", lambda self, name: True)
        collected: list = []
        chain: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, require_output=True,
            collected_text=collected, chain=chain,
        )
        assert collected == [summary]
        assert "get_inventory" not in mind.executed_tools
        assert "craft_item" not in mind.executed_tools
        assert mind.llm_calls == 2
        assert sum(m.get("role") == "tool" for m in chain) == 4

    @pytest.mark.parametrize("empty_result", [end_reply_result(), text_result("")])
    async def test_recovery_only_gets_one_round(self, empty_result) -> None:
        mind = _reflect_mind([end_reply_result(), empty_result])
        collected: list = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, require_output=True, collected_text=collected,
        )
        assert mind.llm_calls == 2
        assert not collected

    async def test_recovery_cannot_exceed_original_budget(self) -> None:
        mind = _reflect_mind([end_reply_result()])
        completion: dict = {}
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, require_output=True,
            safety_limit=1, completion=completion,
        )
        assert mind.llm_calls == 1
        assert completion["reason"] == "budget_exhausted"

    async def test_optional_reflection_can_still_end_silently(self) -> None:
        mind = _reflect_mind([end_reply_result()])
        await run_think_loop(mind, mode=ThinkMode.REFLECT)
        assert mind.llm_calls == 1

    async def test_recovery_allows_text_in_force_tool_mode(self) -> None:
        mind = FakeMind(
            rounds=[end_reply_result(), text_result("已确认结果")], default_text=None,
            config_overrides={"force_tool_use": True},
        )
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, require_output=True,
            tools=[{"type": "function", "function": {"name": "end_reply", "parameters": {}}}],
        )
        assert mind.tool_choices == ["required", None]

    @pytest.mark.parametrize("steer_mode", ["steer", "after"])
    async def test_new_instructions_can_resume_work(self, monkeypatch, steer_mode) -> None:
        from agent.delegation.steer import SteerInbox, bind_steer_drain

        inbox = SteerInbox()
        mind = _reflect_mind([
            end_reply_result(), text_result("第一阶段总结"),
            tool_result("", ["get_state"]), tool_result("补充查询已核验", ["end_reply"]),
        ])
        original_invoke = mind._invoke_llm_unified

        async def invoke(*args, **kwargs):
            result = await original_invoke(*args, **kwargs)
            trigger_round = 1 if steer_mode == "steer" else 2
            if mind.llm_calls == trigger_round:
                inbox.push("summary-test", "请再查询当前状态", mode=steer_mode)
            return result

        monkeypatch.setattr(mind, "_invoke_llm_unified", invoke)
        with bind_steer_drain(lambda mode: inbox.drain("summary-test", mode)):
            await run_think_loop(mind, mode=ThinkMode.REFLECT, require_output=True)
        assert mind.executed_tools.count("get_state") == 1
        assert mind.llm_calls == 4


class TestCompletionReason:
    async def test_reply_budget_records_unfinished_without_delivery(self, anything, deliver_mock) -> None:
        mind = FakeMind(rounds=[text_result("我马上去制作，做好告诉你。")], default_text=None)
        completion: dict = {}
        await run_think_loop(
            mind, mode=ThinkMode.REPLY, anything=anything, base_messages=_base(),
            safety_limit=1, completion=completion,
        )
        assert completion["reason"] == "budget_exhausted"
        deliver_mock.assert_not_awaited()

    async def test_tool_rounds_cannot_complete_plan_and_budget_cancels(self, store) -> None:
        from agent.mind.tool_activation import bind_scope, reset_scope
        from agent.planning import tracker

        scope = "reflect:plan-evidence"
        tracker.planning_store_port.set(store)
        token = bind_scope(scope)
        try:
            plan_id = await tracker.submit_plan(scope, "制作木镐", tracker.parse_steps("查背包|放工作台|合成"))
            mind = _reflect_mind([tool_result("", ["recall"]) for _ in range(3)])
            await run_think_loop(mind, mode=ThinkMode.REFLECT, safety_limit=3, base_messages=_base())
            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal is not None
            assert goal["status"] == "cancelled"
            assert [step["status"] for step in goal["steps"]] == ["skipped"] * 3
        finally:
            reset_scope(token)
            tracker.planning_store_port.unbind()

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
        assert "可能已执行" in result.error
        assert "先只读核验" in result.error


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


class TestAfterBoundary:
    """收束边界 after 档：子代理本要结束时注入追加指令续跑。"""

    async def test_after_message_extends_reflect(self) -> None:
        from agent.delegation.steer import SteerInbox, bind_steer_drain

        inbox = SteerInbox()
        inbox.push("da", "补充：顺便统计总数", mode="after")
        mind = _reflect_mind([
            text_result("初版结论。"),
            text_result("初版结论。"),
            text_result("初版结论。"),
            # after 注入后继续：再 3 轮纯文本收束
            text_result("补充后的完整结论。"),
            text_result("补充后的完整结论。"),
            text_result("补充后的完整结论。"),
        ])
        collected: list = []
        chain: list = []
        with bind_steer_drain(lambda mode: inbox.drain("da", mode)):
            await run_think_loop(
                mind, mode=ThinkMode.REFLECT, base_messages=_base(),
                collected_text=collected, chain=chain,
            )
        # 6 轮全跑完（after 注入把收束推迟了一轮周期）
        assert mind.llm_calls == 6
        assert any(
            m.get("role") == "user" and "追加指令" in str(m.get("content"))
            for m in chain
        )
        # 产出含注入后的最终段
        assert "补充后的完整结论。" in "".join(collected)

    async def test_no_after_messages_unchanged(self) -> None:
        from agent.delegation.steer import bind_steer_drain

        mind = _reflect_mind([text_result("结论。") for _ in range(3)])
        collected: list = []
        with bind_steer_drain(lambda mode: []):
            await run_think_loop(
                mind, mode=ThinkMode.REFLECT, base_messages=_base(),
                collected_text=collected,
            )
        assert mind.llm_calls == 3

    async def test_after_message_intercepts_end_reply(self) -> None:
        """end_reply 收束边界同样消费 after 档：追加指令注入续跑，
        而非随收束清箱静默丢弃（回归：此前 end_reply 路径不 drain）。"""
        from agent.delegation.steer import SteerInbox, bind_steer_drain

        inbox = SteerInbox()
        inbox.push("da", "补充：顺便统计总数", mode="after")
        mind = _reflect_mind([
            end_reply_result(),
            # after 注入续跑后：再 3 轮纯文本按常规收束
            text_result("补充后的完整结论。"),
            text_result("补充后的完整结论。"),
            text_result("补充后的完整结论。"),
        ])
        collected: list = []
        chain: list = []
        with bind_steer_drain(lambda mode: inbox.drain("da", mode)):
            await run_think_loop(
                mind, mode=ThinkMode.REFLECT, base_messages=_base(),
                collected_text=collected, chain=chain,
            )
        assert mind.llm_calls == 4  # end_reply 未立即收束
        assert any(
            m.get("role") == "user" and "追加指令" in str(m.get("content"))
            for m in chain
        )
        assert "补充后的完整结论。" in "".join(collected)
        assert not inbox.drain("da", "after")  # 收束时箱已空，无静默丢弃


class TestCompletionMessages:
    """completion 容器带出最终消息链（transcript 持久化数据源）。"""

    async def test_messages_include_base_and_chain(self) -> None:
        mind = _reflect_mind([
            tool_result("", ["recall"]),
            text_result("最终结论"),
            text_result("最终结论"),
            text_result("最终结论"),
        ])
        completion: dict = {}
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT,
            base_messages=[{"role": "user", "content": "分析"}],
            completion=completion,
        )
        messages = completion["messages"]
        assert messages[0] == {"role": "user", "content": "分析"}
        # 工具调用与结果成对在场（续跑回放需要的完整链）
        roles = [m["role"] for m in messages]
        assert "assistant" in roles and "tool" in roles
        assert messages[-1]["role"] == "assistant"
