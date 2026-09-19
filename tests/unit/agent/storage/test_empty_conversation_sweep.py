"""空会话判定与存量清理测试（sweep_empty_conversations）。

空会话 = 从未有过 user 角色消息的会话（AI 主动搭话历史缺陷与一次性通知
写入产生）。清理只动空会话的过期消息/摘要/回复检查点；有用户消息的会话
与宽限窗口内的新会话不受影响。
"""

from __future__ import annotations

import time

from agent.storage.sqlite_backend import SqliteBackend


async def _seed(
    sqlite: SqliteBackend, scope_type: str, scope_id: str, roles: list[str], *, ts_ns: int,
) -> None:
    for role in roles:
        await sqlite.append_conversation(
            scope_type=scope_type, scope_id=scope_id, role=role,
            content=f"[{role}] {scope_id}", ts_ns=ts_ns, adapter_key="qq",
        )


class TestConversationHasUserMessage:
    async def test_empty_conversation_detected(self, sqlite: SqliteBackend) -> None:
        await _seed(sqlite, "user", "qq:1", ["assistant", "system"], ts_ns=100)
        assert await sqlite.conversation_has_user_message(scope_type="user", scope_id="qq:1") is False

    async def test_interacted_conversation_detected(self, sqlite: SqliteBackend) -> None:
        await _seed(sqlite, "user", "qq:2", ["assistant", "user"], ts_ns=100)
        assert await sqlite.conversation_has_user_message(scope_type="user", scope_id="qq:2") is True

    async def test_scope_isolated(self, sqlite: SqliteBackend) -> None:
        """同 base id 不同子会话（#chat）互不影响——子会话空不拖累主会话。"""
        await _seed(sqlite, "user", "qq:3", ["user"], ts_ns=100)
        assert await sqlite.conversation_has_user_message(
            scope_type="user", scope_id="qq:3#chat1") is False


class TestSweepEmptyConversations:
    async def test_sweep_removes_stale_empty_only(self, sqlite: SqliteBackend) -> None:
        """过期空会话清除；有用户消息的会话与宽限窗口内的空会话保留。"""
        old = time.time_ns() - 7200 * 1_000_000_000
        now = time.time_ns()

        await _seed(sqlite, "user", "qq:empty", ["assistant", "system"], ts_ns=old)
        await _seed(sqlite, "user", "qq:alive", ["user", "assistant"], ts_ns=old)
        await _seed(sqlite, "group", "qq:group-alive", ["user"], ts_ns=old)
        await _seed(sqlite, "user", "qq:fresh-empty", ["system"], ts_ns=now)

        report = await sqlite.sweep_empty_conversations()

        assert report["scopes"] == 1
        assert report["messages"] == 2
        assert await sqlite.count_conversation(scope_type="user", scope_id="qq:empty") == 0
        assert await sqlite.count_conversation(scope_type="user", scope_id="qq:alive") == 2
        assert await sqlite.count_conversation(scope_type="group", scope_id="qq:group-alive") == 1
        # 宽限窗口内的空会话等下一轮
        assert await sqlite.count_conversation(scope_type="user", scope_id="qq:fresh-empty") == 1

    async def test_sweep_cleans_summary_and_checkpoint(self, sqlite: SqliteBackend) -> None:
        """空会话的摘要与残留回复检查点一并清除；在飞会话的保留。"""
        old = time.time_ns() - 7200 * 1_000_000_000
        await _seed(sqlite, "user", "qq:empty", ["assistant"], ts_ns=old)
        await _seed(sqlite, "user", "qq:alive", ["user"], ts_ns=old)
        await sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="qq:empty", summary="s1",
            watermarks={}, folded_count=1,
        )
        await sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="qq:alive", summary="s2",
            watermarks={}, folded_count=1,
        )
        await sqlite.record_reply_checkpoint("user_qq:empty", adapter_key="qq", phase="reply")
        await sqlite.record_reply_checkpoint("user_qq:alive", adapter_key="qq", phase="reply")

        report = await sqlite.sweep_empty_conversations()

        assert report["summaries"] == 1
        assert report["checkpoints"] == 1
        assert await sqlite.get_conversation_summary(scope_type="user", scope_id="qq:empty") is None
        assert await sqlite.get_conversation_summary(scope_type="user", scope_id="qq:alive") is not None
        keys = {row["scope_key"] for row in await sqlite.load_reply_checkpoints()}
        assert keys == {"user_qq:alive"}

    async def test_sweep_idempotent(self, sqlite: SqliteBackend) -> None:
        old = time.time_ns() - 7200 * 1_000_000_000
        await _seed(sqlite, "user", "qq:empty", ["assistant"], ts_ns=old)
        first = await sqlite.sweep_empty_conversations()
        second = await sqlite.sweep_empty_conversations()
        assert first["scopes"] == 1
        assert second == {"scopes": 0, "messages": 0, "summaries": 0, "checkpoints": 0}
