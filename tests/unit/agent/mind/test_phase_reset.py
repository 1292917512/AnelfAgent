"""Mind.phase 空闲归零：工作完成边界统一经 _reset_phase_if_idle 收口。

回归背景：phase 复位曾只存在于 execute_reply finally 一处，心跳 idle 决策、
reflect 族（任务/反思/子代理）、PROACTIVE 直调、未入队消息四条路径结束后
阶段永久卡死（UI 恒显「决策中」）。本文件锁定活跃事实源三元组
（_active_scopes / _reflect_depth / _cycle_lock）与各完成边界的收口行为。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from agent.config import MindConfig
from agent.mind import cycle
from agent.mind import mind as mind_module
from agent.mind.autonomous import MindPhase
from agent.mind.interrupt import InterruptRegistry
from agent.mind.mind import Mind


def _bare_mind() -> Any:
    """最小 Mind 实例（绕过构造）：phase 判定触达的字段 + 真实 _set_phase/收口。"""
    mind = Mind.__new__(Mind)
    mind.config = MindConfig()
    mind.phase = MindPhase.IDLE
    mind._active_scopes = set()
    mind._reflect_depth = 0
    mind._cycle_lock = asyncio.Lock()
    return mind


def _phase_log(mind: Any) -> List[MindPhase]:
    """包装真实 _set_phase，记录相位流转序列。"""
    log: List[MindPhase] = []
    real = mind._set_phase

    def _wrapped(phase: MindPhase) -> None:
        log.append(phase)
        real(phase)

    mind._set_phase = _wrapped
    return log


class TestResetPhaseIfIdle:
    def test_all_idle_resets_to_idle(self) -> None:
        mind = _bare_mind()
        mind.phase = MindPhase.DECIDING
        mind._reset_phase_if_idle()
        assert mind.phase is MindPhase.IDLE

    def test_already_idle_is_noop(self) -> None:
        """已是 IDLE 时不重复设置（避免无谓的相位变更事件）。"""
        mind = _bare_mind()
        log = _phase_log(mind)
        mind._reset_phase_if_idle()
        assert log == []

    def test_active_scope_blocks_reset(self) -> None:
        mind = _bare_mind()
        mind.phase = MindPhase.REPLYING
        mind._active_scopes.add("user_webui:u1")
        mind._reset_phase_if_idle()
        assert mind.phase is MindPhase.REPLYING

    def test_reflect_depth_blocks_reset(self) -> None:
        mind = _bare_mind()
        mind.phase = MindPhase.LLM_CALLING
        mind._reflect_depth = 1
        mind._reset_phase_if_idle()
        assert mind.phase is MindPhase.LLM_CALLING

    async def test_cycle_lock_blocks_reset(self) -> None:
        mind = _bare_mind()
        mind.phase = MindPhase.DECIDING
        async with mind._cycle_lock:
            mind._reset_phase_if_idle()
            assert mind.phase is MindPhase.DECIDING
        mind._reset_phase_if_idle()
        assert mind.phase is MindPhase.IDLE


class TestExecuteMindPhaseReset:
    async def test_cycle_end_resets_deciding(self) -> None:
        """心跳 idle 决策路径：周期内标记 DECIDING，锁释放后归零。"""
        mind = _bare_mind()

        async def _cycle(*, is_heartbeat: bool = False) -> None:
            mind._set_phase(MindPhase.DECIDING)

        mind._autonomous_cycle = _cycle
        await mind.execute_mind(is_heartbeat=True)
        assert mind.phase is MindPhase.IDLE

    async def test_cycle_end_with_active_scope_keeps_phase(self) -> None:
        """周期派生了存活的后台回复（已登记 scope）时不归零。"""
        mind = _bare_mind()

        async def _cycle(*, is_heartbeat: bool = False) -> None:
            mind._set_phase(MindPhase.DECIDING)
            mind._active_scopes.add("user_webui:u1")

        mind._autonomous_cycle = _cycle
        await mind.execute_mind()
        assert mind.phase is MindPhase.DECIDING

    async def test_try_execute_mind_resets_after_cycle(self) -> None:
        mind = _bare_mind()

        async def _cycle(*, is_heartbeat: bool = False) -> None:
            mind._set_phase(MindPhase.DECIDING)

        mind._autonomous_cycle = _cycle
        await mind.try_execute_mind()
        assert mind.phase is MindPhase.IDLE

    async def test_cycle_exception_still_resets(self) -> None:
        """周期异常穿透（assistant 捕获续跑）：finally 收口不留 DECIDING 卡死。"""
        mind = _bare_mind()

        async def _cycle(*, is_heartbeat: bool = False) -> None:
            mind._set_phase(MindPhase.DECIDING)
            raise RuntimeError("boom")

        mind._autonomous_cycle = _cycle
        with pytest.raises(RuntimeError):
            await mind.execute_mind()
        assert mind.phase is MindPhase.IDLE


class TestCycleBodyDeciding:
    async def test_deciding_covers_situation_gather(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DECIDING 在态势收集前生效：收集期间不显示 idle。"""
        from agent.mind import cycle as cycle_mod
        from agent.mind.autonomous import Decision, DecisionType, PendingMessage, SituationContext

        mind = _bare_mind()
        seen: List[MindPhase] = []
        situation = SituationContext(
            pending_messages=[PendingMessage(scope="user_webui:u1", preview="hi")],
            active_goals=["g1"],
        )

        async def _gather(_mind, *, is_heartbeat: bool = False) -> SituationContext:
            seen.append(mind.phase)
            return situation

        mind._heartbeat_running = False
        mind.pfc = SimpleNamespace(
            peek_all_tasks=lambda: [("user_webui:u1", 1, 0, "hi")],
            peek_general_tasks=lambda: [],
            pending_analysis=[],
            get_adapter_key=lambda scope: "webui",
            get_pending_signal=lambda scope: None,
        )
        monkeypatch.setattr(cycle, "active_goal_lines", AsyncMock(return_value=["g1"]))
        monkeypatch.setattr(cycle, "gather_situation", _gather)
        monkeypatch.setattr(cycle, "think_and_decide", AsyncMock(
            return_value=[Decision(type=DecisionType.IDLE, reason="r")],
        ))
        monkeypatch.setattr(cycle, "execute_decisions", AsyncMock())
        monkeypatch.setattr(cycle_mod.event_bus, "emit", AsyncMock())

        await cycle_mod.run_cycle(mind, {}, is_heartbeat=False)
        assert seen == [MindPhase.DECIDING]
        cycle.execute_decisions.assert_awaited_once()


class TestReflectPhaseReset:
    def _wire_reflect_fakes(self, mind: Any) -> None:
        mind.note_activity = lambda: None
        mind.config = SimpleNamespace(max_tool_iterations=6)
        mind.pfc = SimpleNamespace(
            get_active_tool_schemas=AsyncMock(return_value=[]),
            clear_dynamic_tools=lambda **kwargs: None,
        )

    async def test_reflect_end_resets_phase_and_depth(self, monkeypatch) -> None:
        """reflect 结束（任务/反思/子代理统一入口）：深度复位 + 相位归零。"""
        mind = _bare_mind()
        self._wire_reflect_fakes(mind)

        async def _loop(_mind, **kwargs: Any) -> None:
            mind._set_phase(MindPhase.LLM_CALLING)

        monkeypatch.setattr(mind_module, "think_loop", _loop)
        output = await mind.reflect([{"role": "user", "content": "x"}])
        assert output == ""
        assert mind._reflect_depth == 0
        assert mind.phase is MindPhase.IDLE

    @pytest.mark.parametrize("allow", [None, True, False])
    async def test_reflect_output_permission_is_separate_from_text_routing(self, monkeypatch, allow) -> None:
        mind = _bare_mind()
        self._wire_reflect_fakes(mind)
        monkeypatch.setattr(mind_module, "think_loop", AsyncMock())
        kwargs = {} if allow is None else {"allow_output_tools": allow}
        await mind.reflect([{"role": "user", "content": "检查后按需通知"}], **kwargs)
        call = mind_module.think_loop.await_args.kwargs
        assert ("send_message" in call["blocked_tools"]) is (allow is False)
        assert "list_channels" not in call["blocked_tools"]
        assert call["anything"] is None
        assert call["mode"].value == "reflect"

    @pytest.mark.parametrize("needs_skill_tool", [False, True])
    async def test_inherited_directory_keeps_order_and_required_tools(self, needs_skill_tool: bool, monkeypatch) -> None:
        mind = _bare_mind()
        self._wire_reflect_fakes(mind)
        inherited = [{"type": "function", "function": {"name": name}} for name in ("send_message", "end_reply")]
        from core.entity import EntityRegistry
        monkeypatch.setattr(EntityRegistry, "get_active_tools", AsyncMock(return_value=[SimpleNamespace(name=name) for name in ("send_message", "end_reply")]))
        monkeypatch.setattr(EntityRegistry, "get_tool_schema_by_names", lambda names: inherited)
        selected = [{"type": "function", "function": {"name": "end_reply"}}]
        if needs_skill_tool:
            selected.append({"type": "function", "function": {"name": "get_skill"}})
        mind.pfc.get_reflect_tool_schemas = AsyncMock(return_value=selected)
        loop = AsyncMock()
        monkeypatch.setattr(mind_module, "think_loop", loop)
        await mind.reflect([{"role": "user", "content": "review"}], tool_tags=["skills"],
                           inherited_tools=inherited, extra_blocked_tools={"send_message"})
        sent = loop.await_args.kwargs
        assert sent["active_tools"][:2] == inherited
        assert len(sent["active_tools"]) == 2 + needs_skill_tool
        assert "send_message" in sent["blocked_tools"]
        assert sent["reflect_tool_selectors"] == ["skills"]
        sent["active_tools"][0]["function"]["name"] = "mutated"
        assert inherited[0]["function"]["name"] == "send_message"

    async def test_concurrent_reflects_reset_only_after_last(self, monkeypatch) -> None:
        """并发 reflect：先结束的不归零（深度仍存活），最后一个收尾才归零。"""
        mind = _bare_mind()
        self._wire_reflect_fakes(mind)
        entered = 0
        release = asyncio.Event()

        async def _loop(_mind, **kwargs: Any) -> None:
            nonlocal entered
            entered += 1
            mind._set_phase(MindPhase.LLM_CALLING)
            if entered == 1:
                await release.wait()

        monkeypatch.setattr(mind_module, "think_loop", _loop)
        first = asyncio.create_task(mind.reflect([{"role": "user", "content": "a"}]))
        await asyncio.sleep(0)
        second = asyncio.create_task(mind.reflect([{"role": "user", "content": "b"}]))
        await asyncio.sleep(0)
        await second
        # 第二个先结束：第一个仍持有深度，相位不归零
        assert mind._reflect_depth == 1
        assert mind.phase is MindPhase.LLM_CALLING
        release.set()
        await first
        assert mind._reflect_depth == 0
        assert mind.phase is MindPhase.IDLE

    async def test_reflect_exception_still_resets(self, monkeypatch) -> None:
        """reflect 异常穿透：深度与相位同样复位，不留卡死。"""
        mind = _bare_mind()
        self._wire_reflect_fakes(mind)

        async def _loop(_mind, **kwargs: Any) -> None:
            mind._set_phase(MindPhase.TOOL_EXECUTING)
            raise RuntimeError("boom")

        monkeypatch.setattr(mind_module, "think_loop", _loop)
        with pytest.raises(RuntimeError):
            await mind.reflect([{"role": "user", "content": "x"}])
        assert mind._reflect_depth == 0
        assert mind.phase is MindPhase.IDLE


class TestReplyPhaseReset:
    async def test_direct_reply_resets_phase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PROACTIVE 直调路径（无 scope 登记）：reply 返回即归零。"""
        mind = _bare_mind()
        mind.note_activity = lambda: None

        async def _tl(m: Any, anything: Any, images: Any, *, adapter_key: str, completion: dict) -> None:
            m._set_phase(MindPhase.REPLYING)

        monkeypatch.setattr("agent.mind.mind.reply_entry", _tl)
        await mind.reply(SimpleNamespace(entity_scope="user_webui:u1"))
        assert mind.phase is MindPhase.IDLE

    async def test_registered_reply_keeps_phase_until_scope_released(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_reply 路径：reply 内层不收口（scope 仍登记），注销后才归零。"""
        mind = _bare_mind()
        mind.note_activity = lambda: None
        scope = "user_webui:u1"
        mind._active_scopes.add(scope)

        async def _tl(m: Any, anything: Any, images: Any, *, adapter_key: str, completion: dict) -> None:
            m._set_phase(MindPhase.REPLYING)

        monkeypatch.setattr("agent.mind.mind.reply_entry", _tl)
        await mind.reply(SimpleNamespace(entity_scope=scope))
        assert mind.phase is MindPhase.REPLYING
        # execute_reply finally：注销 scope → 收口归零
        mind._active_scopes.discard(scope)
        mind._reset_phase_if_idle()
        assert mind.phase is MindPhase.IDLE


class TestAcceptFeelPhaseReset:
    async def test_duplicate_delivery_does_not_enqueue_again(self) -> None:
        mind = _bare_mind()
        self._wire_accept_fakes(mind)
        mind.add_conversation.return_value = False
        await mind.accept_feel(self._anything(trigger_mind=True))
        mind.pfc.add_task.assert_not_awaited()
        assert mind.phase is MindPhase.IDLE

    def _wire_accept_fakes(self, mind: Any) -> None:
        mind.add_conversation = AsyncMock()
        mind._reflecting = False
        mind._auto_cycle_retry = 0
        mind.wake_budget = SimpleNamespace(reset=lambda scope: None)
        mind._channel_snapshots = {}
        mind.interrupts = InterruptRegistry()
        mind.pfc = SimpleNamespace(add_task=AsyncMock())

    @staticmethod
    def _anything(*, trigger_mind: bool) -> Any:
        return SimpleNamespace(
            entity_scope="user_webui:u1",
            trigger_mind=trigger_mind,
            char_type=None,
            adapter_key="webui",
            images=None,
            media_segments=None,
            get_text_content=lambda: "你好",
            __str__=lambda self: "你好",
        )

    async def test_non_trigger_message_resets_accepting(self) -> None:
        """未入队消息（trigger_mind=False）：ACCEPTING 标记后由尾部收口归零。"""
        mind = _bare_mind()
        self._wire_accept_fakes(mind)
        await mind.accept_feel(self._anything(trigger_mind=False))
        assert mind.phase is MindPhase.IDLE

    @pytest.mark.parametrize("text", [
        "你好", "/deploy-check 检查部署", "停止", "停下", "别说了", "取消",
        "stop!", "Cancel", "别再提加班", "不要再聊股票",
    ])
    @pytest.mark.parametrize("active", [False, True])
    async def test_chat_text_is_recorded_and_enqueued_without_interrupt(
            self, text: str, active: bool) -> None:
        """聊天正文按原文入队，进行中的会话不因固定话语触发中断。"""
        mind = _bare_mind()
        self._wire_accept_fakes(mind)
        message = self._anything(trigger_mind=True)
        message.get_text_content = lambda: text
        if active:
            mind._active_scopes.add(message.entity_scope)
            mind.phase = MindPhase.REPLYING
        await mind.accept_feel(message)
        mind.add_conversation.assert_awaited_once_with(message)
        mind.pfc.add_task.assert_awaited_once_with(message)
        assert mind.interrupts.pending_scopes() == []
        assert mind.phase is (MindPhase.REPLYING if active else MindPhase.IDLE)

    async def test_accept_exception_still_resets(self) -> None:
        """感知处理异常（如历史写入失败）：finally 收口不留 ACCEPTING 卡死。"""
        mind = _bare_mind()
        self._wire_accept_fakes(mind)
        mind.add_conversation = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await mind.accept_feel(self._anything(trigger_mind=True))
        assert mind.phase is MindPhase.IDLE
