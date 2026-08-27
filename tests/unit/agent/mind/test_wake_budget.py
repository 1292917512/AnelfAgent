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

    def test_failure_budget_independent(self) -> None:
        """成功与失败各自独立计数：成功额度耗尽不挤占失败额度（失败必达）。"""
        _set_budget(1)
        t = WakeBudgetTracker()
        t.consume("user_qq:1")
        assert not t.allow("user_qq:1")          # 成功额度已耗尽
        assert t.allow("user_qq:1", failed=True)  # 失败额度独立，仍可用
        t.consume("user_qq:1", failed=True)
        assert not t.allow("user_qq:1", failed=True)
        assert t.count("user_qq:1") == 1
        assert t.count("user_qq:1", failed=True) == 1

    def test_reset_clears_both_counters(self) -> None:
        _set_budget(1)
        t = WakeBudgetTracker()
        t.consume("user_qq:1")
        t.consume("user_qq:1", failed=True)
        t.reset("user_qq:1")  # 真人输入到达
        assert t.allow("user_qq:1")
        assert t.allow("user_qq:1", failed=True)


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

    async def test_failure_wakes_after_success_budget_exhausted(self) -> None:
        """成功额度耗尽后，失败完成仍唤醒（独立额度）——监控任务死了必须有人知道。"""
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_unclaimed(mind, "user_qq:1", "任务A", "ok")
        await mind_mod.Mind._on_bg_task_unclaimed(
            mind, "user_qq:1", "监控脚本", "执行超时（>1200s）", success=False)
        # 成功额度不动，失败额度消耗一次
        assert mind.wake_budget.count("user_qq:1") == 1
        assert mind.wake_budget.count("user_qq:1", failed=True) == 1

    async def test_failure_prompt_marks_failed(self) -> None:
        """失败通知带 [后台任务失败] 头部与处置指引，模型可区分成败。"""
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(3)
        await mind_mod.Mind._on_bg_task_unclaimed(
            mind, "user_qq:1", "监控脚本", "执行超时（>1200s）", success=False)
        content = mind.pfc.add_temporary.call_args[0][0]["content"]
        assert "[后台任务失败]" in content
        assert "告知用户" in content

    async def test_non_conversation_scope_falls_back_to_global_bucket(self) -> None:
        """非 conversation scope（心跳任务等）：全局短期记忆桶兜底，不消耗唤醒预算。"""
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_unclaimed(mind, "_global", "监控脚本", "退出码 1", success=False)
        assert mind.pfc.add_temporary.called
        content = mind.pfc.add_temporary.call_args[0][0]["content"]
        assert "[后台任务失败]" in content
        assert mind.wake_budget.count("_global") == 0
        assert mind.wake_budget.count("_global", failed=True) == 0


class TestBgTaskAlert:
    """Mind._on_bg_task_alert：超时提醒写历史并入队唤醒，去留决策留给 AI。"""

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
        return SimpleNamespace(
            pfc=pfc,
            wake_budget=WakeBudgetTracker(),
            try_execute_mind=_async_noop(),
        )

    async def test_alert_notifies_with_decision_hooks(self) -> None:
        """提醒是报告不是命令：事实 + 真假超时甄别引导 + 平铺可用动作（含 task_id）。"""
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_alert(
            mind, "user_qq:1", "下载任务", "已运行 2000s（预期 1800s）", "ab12cd34")
        content = mind.pfc.add_temporary.call_args[0][0]["content"]
        assert "[后台任务进度报告]" in content
        # 先甄别真假超时，而不是推向终止
        assert "本来就费时" in content and "摸鱼" in content
        assert "不吭声也行" in content
        assert "check_background_tasks(task_id=\"ab12cd34\")" in content
        assert "terminate_background_task(task_id=\"ab12cd34\")" in content
        assert mind.wake_budget.count("user_qq:1") == 1

    async def test_alert_global_scope_bucket_only(self) -> None:
        import agent.mind.mind as mind_mod
        mind = self._fake_mind(1)
        await mind_mod.Mind._on_bg_task_alert(mind, "_global", "监控脚本", "已运行 2000s", "ab12cd34")
        assert mind.pfc.add_temporary.called
        assert mind.wake_budget.count("_global") == 0
