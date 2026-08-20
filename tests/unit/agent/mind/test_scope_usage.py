"""会话级用量统计（agent.mind.scope_usage + sqlite_backend.scope_usage）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from agent.mind.scope_usage import ScopeUsageStats
from agent.storage.sqlite_backend import SqliteBackend


def _usage(prompt: int = 100, completion: int = 50, cache_read: int = 80) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cache_read_input_tokens=cache_read,
    )


class TestScopeUsageStats:
    async def test_record_accumulates_and_flushes(self) -> None:
        flushed: list = []

        async def cb(scope, delta):
            flushed.append((scope, dict(delta)))

        s = ScopeUsageStats(flush_callback=cb)
        s.record("user_qq:1", "reply", _usage())
        s.record("user_qq:1", "reply", _usage(prompt=10, completion=5, cache_read=0))
        s.turn("user_qq:1")
        await s.flush("user_qq:1")
        scope, delta = flushed[-1]
        assert scope == "user_qq:1"
        assert delta["llm_calls"] == 2
        assert delta["prompt_tokens"] == 110
        assert delta["total_tokens"] == 165
        assert delta["turns"] == 1

    async def test_flush_failure_rolls_back_pending(self) -> None:
        calls = {"n": 0}

        async def cb(scope, delta):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db down")

        s = ScopeUsageStats(flush_callback=cb)
        s.record("user_qq:1", "reply", _usage())
        await s.flush("user_qq:1")  # 失败：pending 回滚
        await s.flush("user_qq:1")  # 重试：再次携带增量
        assert calls["n"] == 2

    def test_empty_scope_skipped(self) -> None:
        s = ScopeUsageStats()
        s.record("", "reflect", _usage())  # 不抛、不累计
        assert s.snapshot() == {}

    def test_turn_without_record_creates_entry(self) -> None:
        s = ScopeUsageStats()
        s.turn("user_qq:9")
        assert s.snapshot()["user_qq:9"]["turns"] == 1

    def test_snapshot_hides_internal_fields(self) -> None:
        s = ScopeUsageStats()
        s.record("user_qq:1", "reply", _usage())
        snap = s.snapshot()["user_qq:1"]
        assert "_pending" not in snap
        assert snap["llm_calls"] == 1

    async def test_auto_flush_after_threshold(self) -> None:
        from core.config import ConfigManager
        ConfigManager.set("usage_stats_flush_every", 2)
        flushed: list = []

        async def cb(scope, delta):
            flushed.append(scope)

        s = ScopeUsageStats(flush_callback=cb)
        s.record("user_qq:1", "reply", _usage())
        s.record("user_qq:1", "reply", _usage())  # 达阈值 2 → 自动排入 flush
        # create_task 需要事件循环排空——异步测试上下文中已排入
        for _ in range(10):
            import asyncio
            await asyncio.sleep(0)
        assert flushed == ["user_qq:1"]


class TestScopeUsageSqlite:
    async def test_upsert_accumulates(self, tmp_path) -> None:
        sqlite = SqliteBackend(db_path=str(tmp_path / "u.sqlite3"))
        await sqlite._ensure_init()
        try:
            await sqlite.upsert_scope_usage("user_qq:1", {
                "turns": 1, "llm_calls": 2, "prompt_tokens": 100,
                "completion_tokens": 40, "total_tokens": 140, "cache_read_tokens": 60,
            })
            await sqlite.upsert_scope_usage("user_qq:1", {
                "turns": 1, "llm_calls": 3, "prompt_tokens": 50,
                "completion_tokens": 10, "total_tokens": 60, "cache_read_tokens": 30,
            })
            rows = await sqlite.list_scope_usage()
            assert len(rows) == 1
            row = rows[0]
            assert row["turns"] == 2
            assert row["llm_calls"] == 5
            assert row["prompt_tokens"] == 150
            assert row["total_tokens"] == 200
            assert row["cache_read_tokens"] == 90
        finally:
            await sqlite.close()

    async def test_list_orders_by_total_desc(self, tmp_path) -> None:
        sqlite = SqliteBackend(db_path=str(tmp_path / "u2.sqlite3"))
        await sqlite._ensure_init()
        try:
            await sqlite.upsert_scope_usage("user_qq:small", {"total_tokens": 10})
            await sqlite.upsert_scope_usage("user_qq:big", {"total_tokens": 999})
            rows = await sqlite.list_scope_usage()
            assert rows[0]["scope_key"] == "user_qq:big"
        finally:
            await sqlite.close()


class TestEphemeralScopeAndAttribution:
    def test_reflect_scope_not_recorded(self) -> None:
        """reflect:{uuid} 一次性 scope 不建独立统计行（防孤儿挤爆容量）。"""
        stats = ScopeUsageStats()
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                total_tokens=110, cache_read_input_tokens=0)
        stats.record("reflect:ab12cd34", "reflect", usage)
        assert stats.snapshot() == {}

    def test_reflect_scope_turn_not_recorded(self) -> None:
        stats = ScopeUsageStats()
        stats.turn("reflect:ab12cd34")
        assert stats.snapshot() == {}

    def test_usage_scope_bind_roundtrip(self) -> None:
        """bind_usage_scope 绑定/复位往返（委托链归属父会话）。"""
        from agent.mind.scope_usage import (
            bind_usage_scope,
            current_usage_scope,
            reset_usage_scope,
        )
        assert current_usage_scope() == ""
        token = bind_usage_scope("user_qq:parent")
        try:
            assert current_usage_scope() == "user_qq:parent"
        finally:
            reset_usage_scope(token)
        assert current_usage_scope() == ""

    async def test_list_scope_usage_prompt_miss_column(self, tmp_path) -> None:
        """list_scope_usage 输出 prompt_miss_tokens 计算列（防重复计误读）。"""
        sqlite = SqliteBackend(db_path=str(tmp_path / "miss.sqlite3"))
        await sqlite._ensure_init()
        try:
            await sqlite.upsert_scope_usage("user_qq:s", {
                "prompt_tokens": 1000, "cache_read_tokens": 700,
            })
            rows = await sqlite.list_scope_usage()
            assert rows[0]["prompt_miss_tokens"] == 300
        finally:
            await sqlite.close()
