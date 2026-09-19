"""心跳维护的空会话周期清理触发测试（_sweep_empty_conversations_if_due）。

启动后首个心跳必须整合存量空会话（_last_empty_sweep 初值 0）；间隔未到
不重复执行；配置 0 关闭。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import agent.heartbeat.engine as engine_mod
from agent.heartbeat.engine import HeartbeatEngine
from agent.storage.sqlite_backend import SqliteBackend


def _engine_with_sqlite(
    monkeypatch: pytest.MonkeyPatch, sqlite: SqliteBackend,
) -> HeartbeatEngine:
    monkeypatch.setattr(
        engine_mod, "TaskRegistry",
        lambda: SimpleNamespace(
            get=lambda _n: None, task_file_exists=lambda _n: False, list_all=lambda: [],
        ),
    )
    from agent.heartbeat.config import HeartbeatConfig

    monkeypatch.setattr(
        engine_mod, "get_heartbeat_config", lambda: HeartbeatConfig(task_schedules=[]))
    mind = SimpleNamespace(
        conversation_data=SimpleNamespace(
            router=SimpleNamespace(sqlite=sqlite)),
    )
    return HeartbeatEngine(mind)  # type: ignore[arg-type]


def _count_sweep_calls(monkeypatch: pytest.MonkeyPatch, sqlite: SqliteBackend) -> list[dict]:
    """替身为计数存根，返回被调次数列表。"""
    calls: list[dict] = []

    async def _fake_sweep() -> dict:
        calls.append({})
        return {"scopes": 0, "messages": 0, "summaries": 0, "checkpoints": 0}

    monkeypatch.setattr(sqlite, "sweep_empty_conversations", _fake_sweep)
    return calls


class TestSweepTrigger:
    async def test_first_tick_sweeps_existing(
        self, sqlite: SqliteBackend, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """启动后首个心跳即整合存量（不等配置间隔）。"""
        old = time.time_ns() - 7200 * 1_000_000_000
        await sqlite.append_conversation(
            scope_type="user", scope_id="qq:empty", role="assistant",
            content="历史缺陷残留", ts_ns=old, adapter_key="qq")
        engine = _engine_with_sqlite(monkeypatch, sqlite)

        await engine._sweep_empty_conversations_if_due()

        assert await sqlite.count_conversation(scope_type="user", scope_id="qq:empty") == 0

    async def test_interval_not_elapsed_skips(
        self, sqlite: SqliteBackend, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        engine = _engine_with_sqlite(monkeypatch, sqlite)
        engine._last_empty_sweep = time.monotonic()  # 刚执行过
        calls = _count_sweep_calls(monkeypatch, sqlite)

        await engine._sweep_empty_conversations_if_due()
        assert calls == []

    async def test_disabled_config_skips(
        self, sqlite: SqliteBackend, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "core.config.get_config_int",
            lambda key, default: 0 if key == "conversation_empty_sweep_interval_seconds" else default,
        )
        engine = _engine_with_sqlite(monkeypatch, sqlite)
        calls = _count_sweep_calls(monkeypatch, sqlite)

        await engine._sweep_empty_conversations_if_due()
        assert calls == []
