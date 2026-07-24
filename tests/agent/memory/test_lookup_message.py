"""lookup_message：按 [message_id:xxx] 精确查找会话（含窗口外）。"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from agent.memory import tools as memory_tools
from agent.storage.sqlite_backend import SqliteBackend
from core.tags import tag_label


@pytest.fixture
async def sqlite(tmp_path):
    backend = SqliteBackend(db_path=str(tmp_path / "lookup.sqlite3"))
    yield backend
    await backend.close()


def _msg(message_id: str, body: str, *, reply_to: str = "") -> str:
    parts = [
        tag_label("time", "2026年01月01日12时00分00秒"),
        tag_label("message_id", message_id),
    ]
    if reply_to:
        parts.append(tag_label("reply_to", reply_to) + "被引用预览")
    return "".join(parts) + "\n" + body


class TestFindByMessageId:
    async def test_exact_match_ignores_prefix_collision(self, sqlite: SqliteBackend) -> None:
        """``12`` 不得误命中 ``123``。"""
        t0 = time.time_ns()
        await sqlite.append_conversation(
            scope_type="group", scope_id="g1", role="user",
            content=_msg("123", "长 ID"), ts_ns=t0,
        )
        await sqlite.append_conversation(
            scope_type="group", scope_id="g1", role="user",
            content=_msg("12", "短 ID"), ts_ns=t0 + 1,
        )

        hits_12 = await sqlite.find_conversation_by_message_id(
            "12", scope_type="group", scope_id="g1",
        )
        hits_123 = await sqlite.find_conversation_by_message_id(
            "123", scope_type="group", scope_id="g1",
        )

        assert len(hits_12) == 1 and "短 ID" in hits_12[0]["content"]
        assert len(hits_123) == 1 and "长 ID" in hits_123[0]["content"]

    async def test_neighbors_around_target(self, sqlite: SqliteBackend) -> None:
        t0 = time.time_ns()
        for i, mid in enumerate(("a", "b", "c", "d", "e")):
            await sqlite.append_conversation(
                scope_type="user", scope_id="u1", role="user",
                content=_msg(mid, f"正文{mid}"), ts_ns=t0 + i,
            )

        hits = await sqlite.find_conversation_by_message_id(
            "c", scope_type="user", scope_id="u1",
        )
        assert len(hits) == 1
        around = await sqlite.fetch_conversation_around(
            scope_type="user",
            scope_id="u1",
            center_ts_ns=hits[0]["ts_ns"],
            center_id=hits[0]["id"],
            before=1,
            after=1,
        )
        bodies = [r["content"] for r in around]
        assert any("正文b" in c for c in bodies)
        assert any("正文c" in c for c in bodies)
        assert any("正文d" in c for c in bodies)
        assert not any("正文a" in c for c in bodies)
        assert not any("正文e" in c for c in bodies)


class TestLookupMessageTool:
    async def test_lookup_by_reply_to_id(self, sqlite: SqliteBackend) -> None:
        t0 = time.time_ns()
        await sqlite.append_conversation(
            scope_type="group", scope_id="99", role="user",
            content=_msg("orig-1", "原始完整内容"), ts_ns=t0,
        )
        await sqlite.append_conversation(
            scope_type="group", scope_id="99", role="user",
            content=_msg("cur-2", "引用回复", reply_to="orig-1"), ts_ns=t0 + 1,
        )

        with patch.object(memory_tools, "_get_sqlite", return_value=sqlite):
            with patch.object(
                memory_tools, "_resolve_lookup_scope", return_value=("group", "99"),
            ):
                raw = await memory_tools.lookup_message(message_id="orig-1")

        data = json.loads(raw)
        assert data["found"] is True
        assert data["message_id"] == "orig-1"
        assert "原始完整内容" in data["target"]["content"]
        assert any(item.get("is_target") for item in data["context"])

    async def test_not_found(self, sqlite: SqliteBackend) -> None:
        with patch.object(memory_tools, "_get_sqlite", return_value=sqlite):
            with patch.object(
                memory_tools, "_resolve_lookup_scope", return_value=("user", "1"),
            ):
                raw = await memory_tools.lookup_message(message_id="missing")

        data = json.loads(raw)
        assert data["found"] is False
        assert "未找到" in data["message"]
