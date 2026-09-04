"""后台任务注册表与等待意图挂起（think_loop）单元测试。

覆盖：
- BackgroundTaskRegistry：登记/完成/快照/等待/去重/轮内轮外路由
- think_loop 挂起点：等待意图 → 挂起会合，完成注入 / 超时降级 / 预算耗尽后纯文本投递
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
from helpers.think_loop_fakes import (
    FakeMind,
    end_reply_result,
    run_think_loop,
    text_result,
)

from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.tools.think_loop import ThinkMode


@pytest.fixture(autouse=True)
def deliver_mock(monkeypatch):
    """拦截纯文本投递，避免真实频道发送。"""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("agent.mind.tools.think_loop.deliver_text", mock)
    return mock


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

    def test_unclaimed_callback_fires_for_all_scopes(self) -> None:
        """轮外完成对非 conversation scope（_global / reflect:*）也触发回调——
        完成事实不因 scope 静默丢弃（兜底去向由回调侧决定）。"""
        registry = BackgroundTaskRegistry()
        calls: List[tuple] = []
        registry.set_unclaimed_callback(
            lambda scope, desc, summary, success: calls.append((scope, desc, summary, success)),
        )
        tid_global = registry.register("_global", "shell", "监控脚本")
        registry.complete(tid_global, False, "执行超时（>1200s），进程已终止")
        tid_reflect = registry.register("reflect:abc", "shell", "心跳任务里的后台命令")
        registry.complete(tid_reflect, True, "退出码 0")
        assert calls == [
            ("_global", "监控脚本", "执行超时（>1200s），进程已终止", False),
            ("reflect:abc", "心跳任务里的后台命令", "退出码 0", True),
        ]

    def test_alert_timeout_reaches_callback_only_when_running(self) -> None:
        """超时提醒只对仍在运行的任务触发，且不改变任务状态（不置 done）。"""
        registry = BackgroundTaskRegistry()
        alerts: List[tuple] = []
        registry.set_alert_callback(
            lambda scope, desc, detail, tid: alerts.append((scope, desc, detail, tid)))
        tid = registry.register("user_1", "shell", "下载任务", expected_seconds=60)
        registry.alert_timeout(tid, "已运行 70s（预期 60s），还在勤勤恳恳地跑")
        assert alerts == [
            ("user_1", "下载任务", "已运行 70s（预期 60s），还在勤勤恳恳地跑", tid),
        ]
        # 任务状态不受提醒影响：仍运行中
        assert [t.task_id for t in registry.running("user_1")] == [tid]
        # 已完成的任务不再提醒
        registry.complete(tid, True, "退出码 0")
        registry.alert_timeout(tid, "迟到的提醒")
        assert len(alerts) == 1

    def test_terminate_routes_and_errors(self) -> None:
        """terminate：跨会话拒绝 / 不存在 / 无句柄 / killer 受理 / 已结束幂等。"""
        registry = BackgroundTaskRegistry()
        # 不存在
        assert registry.terminate("user_1", "nope")["ok"] is False
        # 跨会话
        tid = registry.register("user_1", "shell", "任务")
        assert registry.terminate("user_2", tid)["ok"] is False
        # 无终止句柄
        assert "不支持终止" in registry.terminate("user_1", tid)["error"]
        # killer 受理：只发信号，终态仍由生产者完成路径登记
        registry.attach_killer(tid, lambda: True)
        result = registry.terminate("user_1", tid)
        assert result["ok"] and result["terminated"]
        assert registry.running("user_1")  # 状态未翻转，等待生产者登记终态
        # 已结束幂等返回
        registry.complete(tid, False, "已被 AI 终止")
        result = registry.terminate("user_1", tid)
        assert result["ok"] and result["already_finished"] and not result["terminated"]

    def test_snapshot_includes_expected_seconds(self) -> None:
        registry = BackgroundTaskRegistry()
        registry.register("user_1", "shell", "有预期", expected_seconds=120)
        registry.register("user_1", "shell", "无预期")
        running = {t["description"]: t for t in registry.snapshot("user_1")["running"]}
        assert running["有预期"]["expected_seconds"] == 120
        assert running["无预期"]["expected_seconds"] == 0


# ==================================================================
# think_loop 挂起点测试
# ==================================================================

class _WaitMind(FakeMind):
    """Mind 替身：首轮输出等待意图文本，之后按队列返回结果。"""

    def __init__(self, wait_text: str = "子代理的后台任务还在跑喵～") -> None:
        super().__init__(config_overrides={
            "force_tool_use": True,
            "background_wait_timeout": 0.2,
            "background_wait_budget": 0.25,
        })
        self.background_tasks = BackgroundTaskRegistry()
        self.interrupts = None
        self._wait_text = wait_text
        self._queue: List = []

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None,
        stream=False, on_delta=None, purpose="reply"):
        self.llm_calls += 1
        if self.llm_calls == 1:
            return text_result(self._wait_text)
        if self._queue:
            return self._queue.pop(0)
        return end_reply_result()


_SEND_MESSAGE_TOOL = [{"type": "function", "function": {"name": "send_message"}}]


def _run_reply(mind, anything, steps: Optional[List[str]] = None, chain: Optional[List] = None):
    return run_think_loop(
        mind, anything=anything, steps=steps, chain=chain,
        safety_limit=10, tools=_SEND_MESSAGE_TOOL,
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

    async def test_wait_timeout_degrades_to_prompt(self, anything) -> None:
        """挂起超时 → 注入「仍在运行」提示，AI 随后正常结束。"""
        mind = _WaitMind()
        mind.background_tasks.register("_global", "delegation", "生成图片")
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        assert any("等待后台任务（timeout" in s for s in steps)
        injected = [m for m in chain if m.get("role") == "system" and "仍未完成" in m.get("content", "")]
        assert injected

    async def test_wait_timeout_zeroes_budget_then_delivers(self, anything, deliver_mock) -> None:
        """挂起超时后预算清零：后续纯文本回落独白路径，独白上限掐断后轮末投递。"""
        mind = _WaitMind()
        mind.background_tasks.register("_global", "delegation", "生成图片")
        mind._queue = [text_result(mind._wait_text) for _ in range(5)]
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        assert any("等待后台任务（timeout" in s for s in steps)
        # 第 1 次挂起超时；预算清零后连续独白达上限掐断，独白轮末保底投递
        assert any("掐断结束" in s for s in steps)
        assert mind.llm_calls == 6
        deliver_mock.assert_awaited_once()
        assert not any(
            "未调用工具" in m.get("content", "")
            for m in chain if m.get("role") == "system"
        )

    async def test_any_text_with_tasks_suspends_once(self, anything) -> None:
        """有后台任务时任意纯文本都先挂起一次；预算耗尽后独白上限掐断收尾。"""
        mind = _WaitMind(wait_text="我今天心情不太好喵，想随便聊聊")
        mind.background_tasks.register("_global", "delegation", "生成图片")
        mind._queue = [text_result("我还是不太舒服") for _ in range(5)]
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        assert sum("等待后台任务" in s for s in steps) == 1
        # 超时提示含任务查询路径
        hints = [m for m in chain if m.get("role") == "system" and "check_background_tasks" in m.get("content", "")]
        assert hints
        assert any("掐断结束" in s for s in steps)

    async def test_no_tasks_text_delivers_and_ends(self, anything, deliver_mock) -> None:
        """无后台任务时纯文本为独白：连续上限掐断，轮末保底投递一次。"""
        mind = _WaitMind()
        mind._queue = [text_result(mind._wait_text) for _ in range(6)]
        steps: List[str] = []
        chain: List = []
        await _run_reply(mind, anything, steps, chain)

        assert mind.llm_calls == 5  # text_without_tool_limit
        assert any("掐断结束" in s for s in steps)
        deliver_mock.assert_awaited_once()
        assert deliver_mock.await_args.args[1] == mind._wait_text
        assert not any(
            "未调用工具" in m.get("content", "")
            for m in chain if m.get("role") == "system"
        )


# ==================================================================
# REFLECT 模式防护测试
# ==================================================================

class TestReflectGuards:
    async def test_reflect_pure_text_stops_at_three(self, anything) -> None:
        """REFLECT 持续输出纯文本：恰好 3 次后结束循环，产出累积在 collected_text。"""
        mind = _WaitMind(wait_text="内心反思草稿")
        mind._queue = [text_result("继续反思") for _ in range(10)]
        collected: List[str] = []
        steps: List[str] = []
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, steps=steps, collected_text=collected,
            safety_limit=100, tools=[{"type": "function", "function": {"name": "recall"}}],
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

        async def spy(messages, tools, anything=None, *, tool_choice=None, options=None,
                      stream=False, on_delta=None, purpose="reply"):
            seen.append(messages[-1]["content"])
            return await original(
                messages, tools, anything, tool_choice=tool_choice, options=options,
                stream=stream, on_delta=on_delta, purpose=purpose,
            )

        mind._invoke_llm_unified = spy
        await run_think_loop(
            mind, mode=ThinkMode.REFLECT, safety_limit=2,
            tools=[{"type": "function", "function": {"name": "recall"}}],
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
        previews: dict = {}
        adapter_keys: dict = {}
        self.pfc = SimpleNamespace(
            add_temporary=lambda clip, scope="": None,
            pending_user=[],
            pending_group=[],
            _message_previews=previews,
            _task_adapter_keys=adapter_keys,
            set_message_preview=lambda scope, preview: previews.__setitem__(scope, preview),
            set_adapter_key=lambda scope, key: adapter_keys.__setitem__(scope, key),
            get_adapter_key=lambda scope: "test",
        )
        # 镜像 Mind._on_bg_task_unclaimed：轮外完成经回调排入回复队列并触发新一轮
        self.background_tasks.set_unclaimed_callback(self._on_bg_task_unclaimed)

    def _on_bg_task_unclaimed(self, scope: str, description: str, summary: str, success: bool = True):
        # 镜像 Mind._on_bg_task_unclaimed（async 版）：返回协程由 registry
        # 在主循环 ensure_future——历史写入完成后才入队触发
        async def _notify() -> None:
            from agent.mind.tools.scheduler import enqueue_scope_reply
            await enqueue_scope_reply(
                self.pfc, scope, self.pfc.get_adapter_key(scope),
                f"后台任务完成: {description[:60]}", summary,
            )
            asyncio.create_task(self.try_execute_mind())
        return _notify()

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
        # 回调经 ensure_future 异步执行：放行协程任务
        for _ in range(6):
            await asyncio.sleep(0)

        # 完成即新 turn：scope 已排入回复队列，并触发新一轮
        assert "user_123" in mind.pfc.pending_user
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
        assert "user_123" not in mind.pfc.pending_user
        mind.try_execute_mind.assert_not_called()
        if bg is not None:
            await asyncio.wait_for(bg, timeout=5)

    async def test_terminate_background_delegation(self) -> None:
        """AI 终止后台委托：killer 线程安全桥回主循环 cancel，按取消终态完成并通知。"""
        from agent.delegation.delegation_manager import DelegationManager

        mind = _DelegationMind()
        manager = DelegationManager(mind)
        # 让子代理持续运行直到被终止
        async def _slow_reflect(*a, **k) -> str:
            await asyncio.sleep(30)
            return "迟到的结果"
        mind.reflect = AsyncMock(side_effect=_slow_reflect)
        delegation_id = manager.delegate_background("长任务", scope="user_123")
        await asyncio.sleep(0.05)
        assert len(mind.background_tasks.running("user_123")) == 1

        result = await asyncio.to_thread(
            mind.background_tasks.terminate, "user_123", delegation_id)
        assert result["ok"] and result["terminated"]
        bg = manager._background_tasks.get(delegation_id)
        if bg is not None:
            await asyncio.wait_for(bg, timeout=5)
        for _ in range(6):
            await asyncio.sleep(0)

        # 取消终态完成 + 轮外通知照常送达
        completed = mind.background_tasks.completed("user_123")
        assert completed and not completed[0].success
        assert "user_123" in mind.pfc.pending_user

    async def test_check_background_tasks_tool(self) -> None:
        """check_background_tasks 工具返回运行中与已完成任务快照。"""
        import json

        from agent.delegation import delegate_tool
        from agent.delegation.delegation_manager import DelegationManager

        mind = _DelegationMind()
        manager = DelegationManager(mind)
        delegate_tool.delegation_manager_port.set(manager)
        mind.background_tasks.register("_global", "delegation", "生成图片")

        result = json.loads(await delegate_tool.check_background_tasks())
        assert len(result["running"]) == 1
        assert result["running"][0]["description"] == "生成图片"
        assert "hint" in result
