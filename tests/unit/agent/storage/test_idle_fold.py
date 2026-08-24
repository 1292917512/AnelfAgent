"""空闲自动折叠 / 折后预热 / fold_conversations 工具 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.storage import conversation_fold
from agent.storage.conversation_fold import ConversationFolder
from agent.storage.data_center import ConversationData
from agent.storage.storage_router import StorageDomain, StorageRouter
from core.config import ConfigManager


@pytest.fixture
async def conv_data(sqlite, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(conversation_fold, "raw_min_messages", lambda: 2)
    yield ConversationData(StorageRouter(sqlite=sqlite), max_size=6)


def _anything() -> SimpleNamespace:
    return SimpleNamespace(scope_type="user", scope_id="1")


async def _append(conv_data: ConversationData, contents: list[str]) -> None:
    base = time_ns()
    for i, content in enumerate(contents):
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content=content, ts_ns=base + i,
        )


def time_ns() -> int:
    import time
    return time.time_ns()


class TestPrewarmHook:
    async def test_prewarm_dispatched_on_fold_success(self, conv_data, monkeypatch) -> None:
        """折叠成功后调用预热钩子（scope_type, scope_id）。"""
        monkeypatch.setattr(conversation_fold, "raw_min_messages", lambda: 2)
        monkeypatch.setattr(conversation_fold, "fold_hysteresis", lambda: 0)
        calls: list[tuple[str, str]] = []

        async def hook(st: str, sid: str) -> None:
            calls.append((st, sid))

        async def fake_summarizer(prompt: str) -> str:
            return "摘要"

        folder = ConversationFolder()
        folder.set_prewarm_hook(hook)
        monkeypatch.setattr(
            ConversationFolder, "_resolve_summarizer",
            staticmethod(lambda: asyncio.sleep(0, result=fake_summarizer)),
        )
        await _append(conv_data, [f"m{i}" for i in range(6)])
        await folder._fold(conv_data, "user", "1", [("user", "1")], {})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == [("user", "1")]

    async def test_prewarm_disabled_by_config(self, conv_data, monkeypatch) -> None:
        monkeypatch.setattr(conversation_fold, "raw_min_messages", lambda: 2)
        monkeypatch.setattr(conversation_fold, "fold_hysteresis", lambda: 0)
        ConfigManager.set("conversation_fold_prewarm", False)
        try:
            calls: list = []
            folder = ConversationFolder()
            folder.set_prewarm_hook(lambda st, sid: calls.append(1) or asyncio.sleep(0))

            async def fake_summarizer(prompt: str) -> str:
                return "摘要"

            monkeypatch.setattr(
                ConversationFolder, "_resolve_summarizer",
                staticmethod(lambda: asyncio.sleep(0, result=fake_summarizer)),
            )
            await _append(conv_data, [f"m{i}" for i in range(6)])
            await folder._fold(conv_data, "user", "1", [("user", "1")], {})
            await asyncio.sleep(0)
            assert not calls
        finally:
            ConfigManager.set("conversation_fold_prewarm", True)


class TestIdleSweep:
    def _engine(self, conv_data) -> object:
        from agent.heartbeat.engine import HeartbeatEngine
        eng = HeartbeatEngine.__new__(HeartbeatEngine)
        eng.mind = SimpleNamespace(conversation_data=conv_data)
        eng._fold_activity_ts = {}
        eng._fold_idle_beats = {}
        return eng

    async def test_idle_beats_then_schedule(self, conv_data, monkeypatch) -> None:
        """连续 N 心跳无外部新消息且积压达派生阈值（M−x）→ 调度折叠；新消息重置计数。"""
        ConfigManager.set("conversation_fold_idle_beats", 2)
        try:
            # conv_data: max_size=6, raw_min=2（fixture 已 patch）→ 派生阈值 4
            eng = self._engine(conv_data)
            scheduled: list[str] = []
            conv_data.schedule_fold = AsyncMock(side_effect=lambda st, sid: scheduled.append(f"{st}:{sid}") or True)  # type: ignore[method-assign]
            await _append(conv_data, ["m1", "m2", "m3", "m4"])

            await eng._maintain_conversation_folds()  # 首次见到 max_ts → 计数 0
            assert not scheduled
            await eng._maintain_conversation_folds()  # 计数 1 < 2
            assert not scheduled
            await eng._maintain_conversation_folds()  # 计数 2 ≥ 2，积压 4 ≥ 4 → 调度
            assert scheduled == ["user:1"]
            # 新外部消息到达 → 计数重置
            await _append(conv_data, ["m5"])
            await eng._maintain_conversation_folds()
            assert len(scheduled) == 1
        finally:
            ConfigManager.set("conversation_fold_idle_beats", 6)

    async def test_below_min_backlog_not_scheduled(self, conv_data) -> None:
        ConfigManager.set("conversation_fold_idle_beats", 1)
        try:
            eng = self._engine(conv_data)
            conv_data.schedule_fold = AsyncMock(return_value=True)  # type: ignore[method-assign]
            await _append(conv_data, ["m1", "m2"])  # 积压 2 < 派生阈值 4
            await eng._maintain_conversation_folds()
            await eng._maintain_conversation_folds()
            conv_data.schedule_fold.assert_not_called()  # type: ignore[union-attr]
        finally:
            ConfigManager.set("conversation_fold_idle_beats", 6)

    async def test_internal_writes_do_not_reset_idle(self, conv_data) -> None:
        """任务/系统写入（role=system/assistant）不算外部活跃，不重置空闲计数。"""
        ConfigManager.set("conversation_fold_idle_beats", 2)
        try:
            eng = self._engine(conv_data)
            scheduled: list[str] = []
            conv_data.schedule_fold = AsyncMock(side_effect=lambda st, sid: scheduled.append(1) or True)  # type: ignore[method-assign]
            await _append(conv_data, ["m1", "m2", "m3", "m4"])
            await eng._maintain_conversation_folds()
            await eng._maintain_conversation_folds()
            # 系统写入（任务触发）：不应重置空闲计数
            await conv_data.router.append(
                StorageDomain.CONVERSATION,
                scope_type="user", scope_id="1", role="system", content="任务输出", ts_ns=time_ns() + 9999,
            )
            await eng._maintain_conversation_folds()  # 计数 2 ≥ 2 → 调度
            assert len(scheduled) == 1
        finally:
            ConfigManager.set("conversation_fold_idle_beats", 6)


class TestFoldTool:
    async def test_all_scope_schedules_backlogged(self) -> None:
        """all 模式：积压达标的会话被调度，未达标的不动。"""
        conv = SimpleNamespace(
            list_scope_activity=AsyncMock(return_value=[("user", "qq:1", 100), ("group", "qq:2", 99)]),
            scope_backlog=AsyncMock(side_effect=lambda st, sid: {"user:qq:1": 30, "group:qq:2": 5}[f"{st}:{sid}"]),
            schedule_fold=AsyncMock(return_value=True),
            fold_idle_min=20,
        )
        conversation_fold.fold_data_port.set(conv)
        import json
        result = json.loads(await conversation_fold.fold_conversations(scope="all"))
        assert result["scheduled"] == ["user:qq:1"]
        assert result["backlogs"] == {"user:qq:1": 30}

    async def test_empty_scope_without_context_errors(self) -> None:
        """无会话上下文且未指定 scope：返回参数错误而非异常。"""
        conversation_fold.fold_data_port.set(SimpleNamespace())
        import json
        result = json.loads(await conversation_fold.fold_conversations(scope=""))
        assert "error" in result

    async def test_low_backlog_skipped(self) -> None:
        conv = SimpleNamespace(
            scope_backlog=AsyncMock(return_value=1),
            schedule_fold=AsyncMock(return_value=True),
            fold_idle_min=20,
        )
        conversation_fold.fold_data_port.set(conv)
        import json
        result = json.loads(await conversation_fold.fold_conversations(scope="user_qq:123"))
        assert result["scheduled"] == []
        conv.schedule_fold.assert_not_called()
