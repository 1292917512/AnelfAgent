"""后台任务唤醒预算（agent.mind.wake_budget + Mind._on_bg_task_unclaimed）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.mind.wake_budget import WakeBudgetTracker
from core.config import ConfigManager


def _set_budget(value: int) -> None:
    ConfigManager.set("background_wake_budget", value)


class TestWakeBudgetTracker:
    def test_default_budget_allows_three_blocks_fourth(self) -> None:
        _set_budget(3)
        t = WakeBudgetTracker()
        for _ in range(3):
            assert t.allow("user_qq:1")
            t.consume("user_qq:1")
        assert not t.allow("user_qq:1")
        assert t.count("user_qq:1") == 3

    def test_reset_restores(self) -> None:
        _set_budget(2)
        t = WakeBudgetTracker()
        t.consume("user_qq:1")
        t.consume("user_qq:1")
        assert not t.allow("user_qq:1")
        t.reset("user_qq:1")  # 真人输入到达
        assert t.allow("user_qq:1")
        assert t.count("user_qq:1") == 0

    def test_scope_isolation(self) -> None:
        _set_budget(1)
        t = WakeBudgetTracker()
        t.consume("user_qq:1")
        assert not t.allow("user_qq:1")
        assert t.allow("user_qq:2")

    def test_zero_disables_budget(self) -> None:
        _set_budget(0)
        t = WakeBudgetTracker()
        for _ in range(10):
            t.consume("user_qq:1")
        assert t.allow("user_qq:1")

    def test_empty_scope_always_allowed(self) -> None:
        _set_budget(1)
        t = WakeBudgetTracker()
        assert t.allow("")


def _async_noop():
    async def _noop() -> None:
        return None
    return _noop


class TestUnclaimedCallbackBudget:
    """Mind._on_bg_task_unclaimed：超预算不入队唤醒，但信息仍写短期记忆。"""

    @staticmethod
    def _fake_mind(budget: int):
        ConfigManager.set("background_wake_budget", budget)
        pfc = SimpleNamespace(
            add_temporary=MagicMock(),
            pending_user=[],
            pending_group=[],
            set_message_preview=MagicMock(),
            set_adapter_key=MagicMock(),
            get_adapter_key=lambda scope: "qq",
        )
        mind = SimpleNamespace(
            pfc=pfc,
            wake_budget=WakeBudgetTracker(),
            try_execute_mind=_async_noop(),
        )
        return mind

    async def test_within_budget_wakes(self) -> None:
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(3)
        await mind_mod.Mind._on_bg_task_unclaimed(
            mind, "user_qq:1", "构建任务", "退出码 0")
        # 通知已投递（无 router 时短期记忆兜底，信息保底）
        assert mind.pfc.add_temporary.called
        assert mind.wake_budget.count("user_qq:1") == 1

    async def test_over_budget_suppresses_wake(self) -> None:
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_unclaimed(mind, "user_qq:1", "任务A", "ok")
        await mind_mod.Mind._on_bg_task_unclaimed(mind, "user_qq:1", "任务B", "ok")
        # 第二次超预算：仍投递通知（第二次 add_temporary 兜底），计数不再增长
        assert mind.pfc.add_temporary.call_count == 2
        assert mind.wake_budget.count("user_qq:1") == 1

    async def test_reset_via_genuine_input(self) -> None:
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_unclaimed(mind, "user_qq:1", "任务A", "ok")
        mind.wake_budget.reset("user_qq:1")  # 真人消息到达（accept_feel 路径）
        assert mind.wake_budget.allow("user_qq:1")
