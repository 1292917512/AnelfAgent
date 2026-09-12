"""档案执行面消费 + 续跑 + 用量归集（agent.delegation 集成路径）单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.profile import AgentFacets
from agent.delegation.sub_agent import SubAgent, extract_json_output


class _FakeMind:
    """最小 Mind 替身：reflect 直接返回结果。"""

    def __init__(self, output: str = "子任务完成报告") -> None:
        self.reflect = AsyncMock(return_value=output)
        self.pfc = type("PFC", (), {"add_temporary": lambda self, clip, scope="": None})()

    def get_model_context_length(self) -> int:
        return 128_000


class TestFacetsConsumption:
    async def test_instructions_and_schema_in_prompt(self) -> None:
        mind = _FakeMind()
        facets = AgentFacets(
            instructions="只读调研，禁止修改文件",
            tool_tags=["heartbeat", "web"],
            blocked_tools=["run_shell_command"],
            output_schema={"summary": "", "facts": []},
        )
        agent = SubAgent(mind, "目标", facets=facets)
        prompt = agent.build_prompt()
        assert "只读调研，禁止修改文件" in prompt
        assert "输出契约" in prompt
        assert "summary" in prompt

        await agent.run()
        kwargs = mind.reflect.call_args.kwargs
        assert kwargs["tool_tags"] == ["heartbeat", "web"]
        assert "run_shell_command" in kwargs["extra_blocked_tools"]

    async def test_no_facets_default_selectors(self) -> None:
        mind = _FakeMind()
        agent = SubAgent(mind, "目标")
        await agent.run()
        kwargs = mind.reflect.call_args.kwargs
        assert "tool_tags" not in kwargs or kwargs["tool_tags"] is None

    async def test_leaf_blocks_merge_with_facets(self) -> None:
        mind = _FakeMind()
        agent = SubAgent(
            mind, "目标", role="leaf",
            facets=AgentFacets(blocked_tools=["web_fetch"]),
        )
        await agent.run()
        blocked = mind.reflect.call_args.kwargs["extra_blocked_tools"]
        assert {"delegate_task", "web_fetch"} <= set(blocked)

    async def test_schema_validation_flags(self) -> None:
        mind = _FakeMind('{"summary": "结论", "facts": ["a"]}')
        agent = SubAgent(
            mind, "目标", facets=AgentFacets(output_schema={"summary": "", "facts": []}),
        )
        result = await agent.run()
        assert result.schema_ok is True

        mind2 = _FakeMind("自由文本，不是 JSON")
        agent2 = SubAgent(
            mind2, "目标", facets=AgentFacets(output_schema={"summary": ""}),
        )
        result2 = await agent2.run()
        assert result2.schema_ok is False
        # 无契约时不评判
        mind3 = _FakeMind("普通总结")
        result3 = await SubAgent(mind3, "目标").run()
        assert result3.schema_ok is None


class TestExtractJsonOutput:
    def test_plain_json(self) -> None:
        assert extract_json_output('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        assert extract_json_output('```json\n{"a": 1}\n```') == {"a": 1}

    def test_embedded_balanced_block(self) -> None:
        text = '结论如下：\n{"summary": "好", "items": [1]}'
        assert extract_json_output(text) == {"summary": "好", "items": [1]}

    def test_invalid_returns_none(self) -> None:
        assert extract_json_output("没有 JSON") is None
        assert extract_json_output("") is None
        assert extract_json_output("[1, 2]") is None  # 数组不是 object


class TestFollowUp:
    def _manager(self, mind) -> DelegationManager:
        return DelegationManager(mind)

    async def test_follow_up_replays_messages(self) -> None:
        from agent.delegation import journal

        messages = [
            {"role": "user", "content": "初始任务指令"},
            {"role": "assistant", "content": "做了调研"},
            {"role": "tool", "tool_call_id": "t1", "content": "工具结果"},
        ]
        journal.save_transcript({
            "delegation_id": "fx1", "goal": "原始目标", "messages": messages,
            "output": "初版结论", "completed_reason": "completed",
            "role": "leaf", "max_iterations": 10, "agent": "", "model_id": "",
            "facets": None,
        })
        mind = _FakeMind("续跑后的新结论")
        manager = self._manager(mind)

        result = await manager.follow_up("fx1", "继续深挖第二部分")
        assert "error" not in result
        sent = mind.reflect.call_args.args[0]
        roles = [m["role"] for m in sent]
        assert roles == ["user", "assistant", "tool", "user"]
        assert "续跑指令" in sent[-1]["content"]
        assert "继续深挖第二部分" in sent[-1]["content"]
        assert result["parent_delegation_id"] == "fx1"

    async def test_follow_up_rejects_missing_transcript(self) -> None:
        manager = self._manager(_FakeMind())
        result = await manager.follow_up("ghost", "继续")
        assert "error" in result
        assert "delegate_task" in result["hint"]

    async def test_follow_up_rejects_running(self) -> None:
        manager = self._manager(_FakeMind())
        manager._running["busy1"] = {"goal": "g"}
        result = await manager.follow_up("busy1", "继续")
        assert "error" in result
        assert "send_to_agent" in result["error"]


class TestUsageAttribution:
    async def test_usage_bucket_attached_to_result(self) -> None:
        from core.event_bus import EVENT_THINKING_LLM_END, event_bus

        mind = _FakeMind("结论")

        async def reflect(messages, **kwargs):
            # 模拟子代理内的一次 LLM 调用（事件在发射方上下文执行 → ContextVar 归属）
            await event_bus.emit(EVENT_THINKING_LLM_END, {
                "duration_ms": 1500,
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            })
            return "结论"

        mind.reflect = reflect
        manager = DelegationManager(mind)
        result = await manager.delegate("带用量的任务")
        assert result.success
        assert result.usage["turns"] == 1
        assert result.usage["input_tokens"] == 100
        assert result.usage["output_tokens"] == 20
        assert result.usage["duration_ms"] == 1500
        # 完成后清理，不泄漏
        assert manager._usage == {}

    async def test_aggregate_contains_usage(self) -> None:
        from agent.delegation.sub_agent import SubAgentResult

        mind = _FakeMind()
        manager = DelegationManager(mind)
        result = SubAgentResult(goal="g", success=True, output="o",
                                usage={"turns": 3, "input_tokens": 10})
        payload = json.loads(manager.aggregate_results([result]))
        assert payload["results"][0]["usage"]["turns"] == 3
