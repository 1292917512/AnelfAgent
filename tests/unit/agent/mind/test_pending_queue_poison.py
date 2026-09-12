"""待处理队列毒丸防护回归测试（2026-09-12 自主循环 0 退避空转事故）。

事故链：日历提醒在无会话上下文时以 scope="_global" 持久化 → 到期经
enqueue_scope_reply 直入 pending_user → 回复路径解析不了该 scope 只能跳过
→ has_pending_tasks 恒真 + 退避归零 → 自主循环无限空转刷屏。

三层防线各自回归：源头（add_reminder 拒绝不可路由 scope）、入口
（enqueue_scope_reply 拒绝入队，见 test_one_shot_history）、兜底
（pop_next_reply_target 就地清除毒丸条目）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.mind.tools import scheduler as scheduler_mod
from agent.mind.tools.decision_executor import pop_next_reply_target
from agent.mind.tools.scheduler import add_reminder
from agent.mind.work_memory import WorkMemory


def _work_memory() -> WorkMemory:
    """真实 WorkMemory（everything_data 仅异步画像路径触达，此处不涉及）。"""
    return WorkMemory(SimpleNamespace())  # type: ignore[arg-type]


def _mind_with(pfc: WorkMemory) -> Any:
    return SimpleNamespace(pfc=pfc, _active_scopes=set())


class TestAddReminderScopeValidation:
    async def test_rejects_unroutable_scopes(self, tmp_path, monkeypatch) -> None:
        """非会话 scope（_global / 空串 / 无前缀）拒绝持久化，抛 ValueError。"""
        monkeypatch.setattr(
            scheduler_mod, "_reminders_path",
            lambda: tmp_path / "reminders.json",
        )
        for bad in ("_global", "", "nonsense"):
            with pytest.raises(ValueError, match="会话 scope"):
                await add_reminder("note", 1e12 + 60, bad)

    async def test_accepts_conversation_scope(self, tmp_path, monkeypatch) -> None:
        """合法会话 scope 正常写入文件。"""
        path = tmp_path / "reminders.json"
        monkeypatch.setattr(scheduler_mod, "_reminders_path", lambda: path)
        reminder = await add_reminder("note", 1e12 + 60, "user_qq:1292917512", "qq")
        assert reminder["scope"] == "user_qq:1292917512"
        assert path.exists()


class TestConsumeScopeTask:
    def test_poison_purged_with_associated_state(self) -> None:
        """user 队列毒丸：消费后队列空、关联状态（预览/路由/未读）一并清理。"""
        wm = _work_memory()
        wm.pending_user.append("_global")
        wm.set_message_preview("_global", "定时提醒: 测试提醒")
        wm.set_adapter_key("_global", "qq")
        wm._unread_counts["_global"] = 3

        assert wm.consume_scope_task("_global") is True
        assert wm.has_pending_tasks() is False
        assert wm.peek_all_tasks() == []
        assert wm.get_adapter_key("_global") == ""

    def test_entry_in_wrong_queue_still_consumed(self) -> None:
        """落在错误队列的条目同样可消费（不按前缀路由，两个队列都查）。"""
        wm = _work_memory()
        wm.pending_group.append("user_qq:1")
        assert wm.consume_scope_task("user_qq:1") is True
        assert wm.has_pending_tasks() is False

    def test_consume_missing_scope_is_false(self) -> None:
        wm = _work_memory()
        assert wm.consume_scope_task("user_qq:404") is False


class TestPopNextReplyTargetPurgesPoison:
    async def test_poison_purged_and_valid_target_returned(self) -> None:
        """毒丸条目被就地清除（循环得以终止），后面的合法 scope 正常返回。"""
        wm = _work_memory()
        wm.pending_user.append("_global")
        wm.pending_user.append("user_qq:1")
        mind = _mind_with(wm)

        target = await pop_next_reply_target(mind)

        assert target is not None
        assert target.uid == 1
        assert target.adapter_key == "qq"
        # 毒丸被清除、合法条目被消费：队列彻底排空，自主循环不再续轮
        assert wm.has_pending_tasks() is False

    async def test_all_poison_leaves_queue_empty(self) -> None:
        """全毒丸队列：一次调用全部清除，返回 None（本轮空转一轮即收敛）。"""
        wm = _work_memory()
        wm.pending_user.append("_global")
        wm.pending_user.append("badscope")
        mind = _mind_with(wm)

        assert await pop_next_reply_target(mind) is None
        assert wm.has_pending_tasks() is False
