"""后台任务注册表与等待意图挂起（think_loop）单元测试。

覆盖：
- BackgroundTaskRegistry：登记/完成/快照/等待/去重/轮内轮外路由
- think_loop 挂起点：等待意图 → 挂起会合，完成注入 / 超时降级 / 不触发独白熔断
- REFLECT 模式：连续纯文本上限 + 输出纪律提示注入
- DelegationManager：后台委托登记注册表、完成后的轮外通知（完成即新 turn）
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.tools.think_loop import ThinkMode, think_loop


# ==================================================================
# BackgroundTaskRegistry 单元测试
# ==================================================================

class TestBackgroundTaskRegistry:
    def test_register_and_running(self) -> None:
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "生成图片")
        running = registry.running("user_1")
        assert [t.task_id for t in running] == [task_id]
        assert running[0].description == "生成图片"
        # scope 隔离
        assert registry.running("user_2") == []

    def test_complete_marks_done(self) -> None:
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "生成图片")
        claimed = registry.complete(task_id, True, "图片已生成")
        assert not claimed  # 无等待者 → 轮外通知路径
        assert registry.running("user_1") == []
        completed = registry.completed("user_1")
        assert len(completed) == 1
        assert completed[0].success and completed[0].summary == "图片已生成"

    def test_complete_idempotent(self) -> None:
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "任务")
        registry.complete(task_id, True, "done")
        # 重复 complete 不报错、状态不翻转
        assert registry.complete(task_id, False, "x") is True
        assert registry.completed("user_1")[0].success is True

    async def test_wait_any_completed(self) -> None:
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "任务")

        async def finisher() -> None:
            await asyncio.sleep(0.05)
            registry.complete(task_id, True, "结果")

        asyncio.create_task(finisher())
        result = await registry.wait_any("user_1", timeout=5)
        assert result.reason == "completed"
        assert [c.task_id for c in result.completions] == [task_id]

    async def test_wait_any_timeout(self) -> None:
        registry = BackgroundTaskRegistry()
        registry.register("user_1", "delegation", "任务")
        t0 = time.monotonic()
        result = await registry.wait_any("user_1", timeout=0.1)
        assert result.reason == "timeout"
        assert time.monotonic() - t0 < 2

    async def test_wait_any_aborted(self) -> None:
        registry = BackgroundTaskRegistry()
        registry.register("user_1", "delegation", "任务")

        async def abort() -> bool:
            return True

        result = await registry.wait_any("user_1", timeout=5, should_abort=abort)
        assert result.reason == "interrupted"
        assert result.completions == []

    async def test_wait_claimed_by_waiter(self) -> None:
        """有等待者时 complete 返回 True（轮内会合），事件不标记已送达前由 wait 消费。"""
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "任务")
        outcomes: List[bool] = []

        async def finisher() -> None:
            await asyncio.sleep(0.05)
            outcomes.append(registry.complete(task_id, True, "结果"))

        asyncio.create_task(finisher())
        result = await registry.wait_any("user_1", timeout=5)
        assert result.reason == "completed"
        assert outcomes == [True]

    async def test_delivered_completions_not_reinjected(self) -> None:
        """已送达的完成事件不会被后续 wait_any 重复返回（消费去重）。"""
        registry = BackgroundTaskRegistry()
        task_id = registry.register("user_1", "delegation", "任务")
        registry.complete(task_id, True, "结果")  # 无等待者 → 已送达（轮外）
        result = await registry.wait_any("user_1", timeout=0.1)
        assert result.reason == "timeout"  # 不会拿到已送达事件

    def test_snapshot(self) -> None:
        registry = BackgroundTaskRegistry()
        running_id = registry.register("user_1", "delegation", "运行中任务")
        done_id = registry.register("user_1", "delegation", "已完成任务")
        registry.complete(done_id, False, "失败原因")
        snapshot = registry.snapshot("user_1")
        assert [t["task_id"] for t in snapshot["running"]] == [running_id]
        assert len(snapshot["completed"]) == 1
        assert snapshot["completed"][0]["success"] is False


# ==================================================================
# think_loop 挂起点测试
# ==================================================================

class _FakePfc:
    def build_execution_context(self, *a, **kw) -> dict:
        return {"role": "system", "content": "exec"}

    def add_temporary(self, clip) -> None:
        pass

    def clear_dynamic_tools(self) -> None:
        pass

    def record_tool_use(self, name: str) -> None:
        pass

    def expand_discovered_tools(self, tool_calls) -> None:
        pass


def _text_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=text, tool_calls=[], reasoning_content="",
        usage=None, raw=None, model="fake",
    )


def _end_reply_result() -> SimpleNamespace:
    return SimpleNamespace(
        content="",
        tool_calls=[SimpleNamespace(
            id="tc1", name="end_reply", arguments="{}",
            raw={"id": "tc1", "type": "function",
                 "function": {"name": "end_reply", "arguments": "{}"}},
        )],
        reasoning_content="", usage=None, raw=None, model="fake",
    )


class _WaitMind:
    """Mind 替身：首轮输出等待意图文本，之后按队列返回结果。"""

    def __init__(self, wait_text: str = "子代理的后台任务还在跑喵～") -> None:
        self.pfc = _FakePfc()
        self.compressor = None
        self.background_tasks = BackgroundTaskRegistry()
        self.interrupts = None
        self.llm_calls = 0
        self._wait_text = wait_text
        self._queue: List[SimpleNamespace] = []
        self._add_system_context = AsyncMock()
        self._reply_adapter_key = ""

    def _resolve_adapter_key(self) -> str:
        return ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            return '{"ok": true}'
        return _exec

    def _set_phase(self, phase) -> None:
        pass

    def _get_mind_config(self):
        return SimpleNamespace(
            llm_timeout=10.0, force_tool_use=True,
            background_wait_timeout=0.2, background_wait_budget=0.25,
        )

    def get_model_context_length(self) -> int:
        return 0

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None):
        self.llm_calls += 1
        if self.llm_calls == 1:
            return _text_result(self._wait_text)
        if self._queue:
            return self._queue.pop(0)
        return _end_reply_result()


@pytest.fixture
def anything():
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0)


def _run_reply(mind, anything, steps: Optional[List[str]] = None, chain: Optional[List] = None):
    return think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=chain if chain is not None else [],
        execution_steps=steps if steps is not None else [],
        start_time=time.time(),
        safety_limit=10,
        collected_text=[],
        active_tools=[{"type": "function", "function": {"name": "send_message"}}],
        anything=anything,
        base_messages=[{"role": "user", "content": "图片好了吗"}],
    )


class TestWaitSuspension:
    async def test_wait_intent_suspends_and_injects_completion(self, anything) -> None:
        """等待意图 + 后台任务完成 → 挂起会合，结果注入循环，不计独白、不熔断。"""
        mind = _WaitMind()
        # 注册一个 0.05s 后完成的后台任务（scope="_global"，测试无 think_session 绑定）
        task_id = mind.background_tasks.register("_global", "delegation", "生成图片")

        async def finisher() -> None:
            await asyncio.sleep(0.05)
            mind.background_tasks.complete(task_id, True, "图片已生成: /tmp/a.png")

        chain: List = []
        steps: List[str] = []
        asyncio.create_task(finisher())
        await _run_reply(mind, anything, steps, chain)

        # 第 1 轮等待文本 → 挂起 → 完成注入 → 第 2 轮 end_reply
        assert mind.llm_calls == 2
        assert any("等待后台任务（completed" in s for s in steps)
        injected = [m for m in chain if m.get("role") == "system" and "后台任务完成" in m.get("content", "")]
        assert injected and "图片已生成" in injected[0]["content"]
        # 独白未入库（等待不是独白；finish_think 的操作摘要不算）
        monologue_saves = [
            c for c in mind._add_system_context.await_args_list
            if "内心独白" in str(c)
        ]
        assert not monologue_saves

    async def test_wait_timeout_degrades_to_prompt(self, anything) -> None:
        """挂起超时 → 注入「仍在运行」提示，AI 随后正常结束，不触发独白熔断。"""
        mind = _WaitMind()
        mind.background_tasks.register("_global", "delegation", "生成图片")
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        assert any("等待后台任务（timeout" in s for s in steps)
        injected = [m for m in chain if m.get("role") == "system" and "仍未完成" in m.get("content", "")]
        assert injected
        assert not any("连续内心独白" in s for s in steps)

    async def test_wait_timeout_zeroes_budget_then_monologue(self, anything) -> None:
        """挂起超时后预算清零：后续纯文本立即按普通独白处理（计数 + 熔断）。"""
        mind = _WaitMind()
        mind.background_tasks.register("_global", "delegation", "生成图片")
        mind._queue = [_text_result(mind._wait_text) for _ in range(5)]
        steps: List[str] = []
        await _run_reply(mind, anything, steps)

        # 第 1 次文本 → 挂起超时（预算清零）；之后 3 次独白熔断
        assert any("等待后台任务（timeout" in s for s in steps)
        assert any("连续内心独白" in s for s in steps)
        assert mind.llm_calls == 4

    async def test_any_text_with_tasks_suspends_once(self, anything) -> None:
        """有后台任务时任意纯文本都先挂起一次（非等待措辞也如此），超时后才计独白。"""
        mind = _WaitMind(wait_text="我今天心情不太好喵，想随便聊聊")
        mind.background_tasks.register("_global", "delegation", "生成图片")
        mind._queue = [_text_result("我还是不太舒服") for _ in range(5)]
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        # 首次文本挂起超时 → 之后 3 次独白熔断；独白提示情境化含任务查询路径
        assert sum("等待后台任务" in s for s in steps) == 1
        assert any("连续内心独白" in s for s in steps)
        hints = [m for m in chain if m.get("role") == "system" and "check_background_tasks" in m.get("content", "")]
        assert hints

    async def test_no_tasks_monologue_unchanged(self, anything) -> None:
        """无后台任务时独白守卫行为与现状一致（3 次熔断）。"""
        mind = _WaitMind()
        mind._queue = [_text_result(mind._wait_text) for _ in range(5)]
        steps: List[str] = []
        await _run_reply(mind, anything, steps)
        assert mind.llm_calls == 3
        assert any("连续内心独白" in s for s in steps)


# ==================================================================
# REFLECT 模式防护测试
# ==================================================================

class TestReflectGuards:
    async def test_reflect_pure_text_stops_at_three(self, anything) -> None:
        """REFLECT 持续输出纯文本：恰好 3 次后结束循环，产出累积在 collected_text。"""
        mind = _WaitMind(wait_text="内心反思草稿")
        mind._queue = [_text_result("继续反思") for _ in range(10)]
        collected: List[str] = []
        steps: List[str] = []
        await think_loop(
            mind,
            mode=ThinkMode.REFLECT,
            tool_chain=[],
            execution_steps=steps,
            start_time=time.time(),
            safety_limit=100,
            collected_text=collected,
            active_tools=[{"type": "function", "function": {"name": "recall"}}],
            anything=None,
            base_messages=[{"role": "user", "content": "反思一下"}],
        )
        assert len(collected) == 3
        assert mind.llm_calls == 3
        assert any("反思连续纯文本" in s for s in steps)

    async def test_reflect_discipline_injected_when_unsupported(self, anything) -> None:
        """端点不支持强制 tool_choice 时，REFLECT 同样注入输出纪律提示。"""
        mind = _WaitMind()
        mind.llm = SimpleNamespace(config=SimpleNamespace(supports_forced_tool_choice=False))
        seen: List[str] = []
        original = mind._invoke_llm_unified

        async def spy(messages, tools, anything=None, *, tool_choice=None, options=None):
            seen.append(messages[-1]["content"])
            return await original(messages, tools, anything, tool_choice=tool_choice, options=options)

        mind._invoke_llm_unified = spy
        await think_loop(
            mind,
            mode=ThinkMode.REFLECT,
            tool_chain=[],
            execution_steps=[],
            start_time=time.time(),
            safety_limit=2,
            collected_text=[],
            active_tools=[{"type": "function", "function": {"name": "recall"}}],
            anything=None,
            base_messages=[{"role": "user", "content": "反思一下"}],
        )
        assert seen
        assert all("输出纪律" in content for content in seen)


# ==================================================================
# DelegationManager 后台委托与注册表集成
# ==================================================================

class _DelegationMind:
    """DelegationManager 集成测试用 Mind 替身。"""

    def __init__(self, output: str = "子任务完成") -> None:
        self.reflect = AsyncMock(return_value=output)
        self.background_tasks = BackgroundTaskRegistry()
        self.try_execute_mind = AsyncMock()
        self.pfc = SimpleNamespace(
            add_temporary=lambda clip: None,
            pending_user=[],
            pending_group=[],
            _message_previews={},
            _task_adapter_keys={},
            get_adapter_key=lambda scope: "test",
        )

    def get_model_context_length(self) -> int:
        return 128_000


class TestDelegationBackgroundIntegration:
    async def test_background_registers_and_notifies_new_turn(self) -> None:
        """无等待者时：完成事件走轮外通知（排入回复队列 + 触发新一轮 + 标记已送达）。"""
        from agent.delegation.delegation_manager import DelegationManager

        mind = _DelegationMind()
        manager = DelegationManager(mind)
        delegation_id = manager.delegate_background("生成图片", scope="user_123")
        assert mind.background_tasks.running("user_123")[0].task_id == delegation_id

        task = manager._background_tasks[delegation_id]
        await asyncio.wait_for(task, timeout=5)

        # 完成即新 turn：scope 已排入回复队列，并触发新一轮
        assert "123" in mind.pfc.pending_user
        mind.try_execute_mind.assert_called_once()
        # 事件已标记送达，后续 wait_any 不会重复返回
        result = await mind.background_tasks.wait_any("user_123", timeout=0.1)
        assert result.reason == "timeout"

    async def test_background_claimed_by_suspension(self) -> None:
        """有等待者时：完成事件走轮内会合，不触发轮外通知。"""
        from agent.delegation.delegation_manager import DelegationManager

        mind = _DelegationMind()
        manager = DelegationManager(mind)
        wait_task = asyncio.create_task(
            mind.background_tasks.wait_any("user_123", timeout=5)
        )
        await asyncio.sleep(0.05)  # 确保 wait_any 已登记等待者
        delegation_id = manager.delegate_background("生成图片", scope="user_123")
        bg = manager._background_tasks.get(delegation_id)

        result = await wait_task
        assert result.reason == "completed"
        assert result.completions[0].task_id == delegation_id
        # 轮内会合：不排入回复队列、不触发新一轮
        assert "123" not in mind.pfc.pending_user
        mind.try_execute_mind.assert_not_called()
        if bg is not None:
            await asyncio.wait_for(bg, timeout=5)

    async def test_check_background_tasks_tool(self) -> None:
        """check_background_tasks 工具返回运行中与已完成任务快照。"""
        import json
        from agent.delegation import delegate_tool
        from agent.delegation.delegation_manager import DelegationManager

        mind = _DelegationMind()
        manager = DelegationManager(mind)
        delegate_tool.register_delegation_tools(manager)
        mind.background_tasks.register("_global", "delegation", "生成图片")

        result = json.loads(await delegate_tool.check_background_tasks())
        assert len(result["running"]) == 1
        assert result["running"][0]["description"] == "生成图片"
        assert "hint" in result
