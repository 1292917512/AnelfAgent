"""换向折叠（conversation_folds）测试：CRUD / 装配过滤 / 服务层折叠与恢复。

语义：折叠段消息不进 LLM 上下文（原位标记 + 弃置摘要），DB 保留可检索，
恢复（active=0）后消息重新进入上下文。
"""

from types import SimpleNamespace

import pytest

from agent.storage.data_center import ConversationData

_SCOPE = ("user", "webui:web_user#c1")


async def _seed(sqlite, texts, scope=_SCOPE) -> list:
    """按顺序写入消息，返回 id 列表。"""
    ids = []
    for text in texts:
        await sqlite.append_conversation(
            scope_type=scope[0], scope_id=scope[1],
            role="user" if text.startswith("u") else "assistant",
            content=text,
        )
        db = await sqlite._get_db()
        cursor = await db.execute("SELECT MAX(id) FROM conversation_messages")
        ids.append(int((await cursor.fetchone())[0]))
    return ids


def _dc(sqlite) -> ConversationData:
    """最小 ConversationData 替身（_apply_redirect_folds 只用 router.sqlite）。"""
    dc = ConversationData.__new__(ConversationData)
    dc.router = SimpleNamespace(sqlite=sqlite)
    return dc


class TestFoldCRUD:
    async def test_create_list_deactivate_roundtrip(self, sqlite) -> None:
        fold_id = await sqlite.create_conversation_fold(
            scope_type=_SCOPE[0], scope_id=_SCOPE[1],
            from_msg_id=10, to_msg_id=25, summary="试过了 A 方案", folded_count=15,
        )
        assert fold_id > 0
        folds = await sqlite.list_conversation_folds(*_SCOPE)
        assert len(folds) == 1
        assert folds[0]["summary"] == "试过了 A 方案"
        assert folds[0]["active"] is True
        assert folds[0]["from_msg_id"] == 10 and folds[0]["to_msg_id"] == 25

        assert await sqlite.deactivate_conversation_fold(fold_id) is True
        assert await sqlite.list_conversation_folds(*_SCOPE) == []
        # 恢复后历史仍可查（active_only=False）
        assert len(await sqlite.list_conversation_folds(*_SCOPE, active_only=False)) == 1
        # 幂等：重复恢复返回 False
        assert await sqlite.deactivate_conversation_fold(fold_id) is False

    async def test_scope_isolation(self, sqlite) -> None:
        await sqlite.create_conversation_fold(
            scope_type="user", scope_id="webui:web_user#other",
            from_msg_id=1, to_msg_id=2, summary="", folded_count=1,
        )
        assert await sqlite.list_conversation_folds(*_SCOPE) == []


class TestApplyRedirectFolds:
    async def test_folded_rows_replaced_by_marker(self, sqlite) -> None:
        ids = await _seed(sqlite, ["u1", "a1", "u2", "a2", "u3", "a3"])
        await sqlite.create_conversation_fold(
            scope_type=_SCOPE[0], scope_id=_SCOPE[1],
            from_msg_id=ids[1], to_msg_id=ids[3],  # 折叠 a1 之后的 u2/a2
            summary="尝试写脚本未成功", folded_count=2,
        )
        rows = await sqlite.fetch_conversation_multi(
            scopes=[_SCOPE], limit=50)
        filtered = await _dc(sqlite)._apply_redirect_folds(_SCOPE[0], _SCOPE[1], rows)

        contents = [r["content"] for r in filtered]
        assert "u2" not in contents and "a2" not in contents
        assert contents[0] == "u1" and contents[1] == "a1"
        marker = next(r for r in filtered if r["role"] == "system")
        assert "换向折叠" in marker["content"]
        assert "2 条消息" in marker["content"]
        assert "尝试写脚本未成功" in marker["content"]
        # 标记位于折叠段原位（a1 之后、u3 之前）
        assert contents.index(marker["content"]) == 2
        assert contents[3] == "u3"

    async def test_no_folds_passthrough(self, sqlite) -> None:
        await _seed(sqlite, ["u1", "a1"])
        rows = await sqlite.fetch_conversation_multi(scopes=[_SCOPE], limit=50)
        filtered = await _dc(sqlite)._apply_redirect_folds(_SCOPE[0], _SCOPE[1], rows)
        assert [r["content"] for r in filtered] == ["u1", "a1"]

    async def test_restored_fold_returns_messages(self, sqlite) -> None:
        ids = await _seed(sqlite, ["u1", "a1", "u2"])
        fold_id = await sqlite.create_conversation_fold(
            scope_type=_SCOPE[0], scope_id=_SCOPE[1],
            from_msg_id=ids[0], to_msg_id=ids[2], summary="", folded_count=2,
        )
        await sqlite.deactivate_conversation_fold(fold_id)
        rows = await sqlite.fetch_conversation_multi(scopes=[_SCOPE], limit=50)
        filtered = await _dc(sqlite)._apply_redirect_folds(_SCOPE[0], _SCOPE[1], rows)
        assert [r["content"] for r in filtered] == ["u1", "a1", "u2"]


class TestFoldService:
    def _runtime(self, sqlite, monkeypatch, summarize="尝试了方案 A 但没走通") -> None:
        """get_runtime/is_ready 替身：data_center.sqlite 用真实库，mind 用假摘要。"""
        import services.chat as chat_mod

        async def _summarize(prompt: str) -> str:
            return summarize

        rt = SimpleNamespace(
            data_center=SimpleNamespace(sqlite=sqlite),
            mind=SimpleNamespace(summarize_text=_summarize),
        )
        monkeypatch.setattr(chat_mod, "get_runtime", lambda: rt)
        monkeypatch.setattr(chat_mod, "is_ready", lambda: True)

    async def test_fold_creates_range_and_summary(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch)
        ids = await _seed(sqlite, ["u1", "a1", "u2", "a2"])
        result = await ChatService().fold_conversation(
            scope_id=_SCOPE[1], from_message_id=ids[0])
        assert result["folded_count"] == 3
        assert result["summary"] == "尝试了方案 A 但没走通"
        folds = await sqlite.list_conversation_folds(*_SCOPE)
        assert folds[0]["from_msg_id"] == ids[0]
        assert folds[0]["to_msg_id"] == ids[3]

    async def test_fold_unknown_message_rejected(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch)
        await _seed(sqlite, ["u1"])
        with pytest.raises(ValueError, match="不存在"):
            await ChatService().fold_conversation(scope_id=_SCOPE[1], from_message_id=99999)

    async def test_fold_without_successors_rejected(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch)
        ids = await _seed(sqlite, ["u1", "a1"])
        with pytest.raises(ValueError, match="没有可折叠的消息"):
            await ChatService().fold_conversation(scope_id=_SCOPE[1], from_message_id=ids[1])

    async def test_fold_inside_existing_fold_rejected(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch)
        ids = await _seed(sqlite, ["u1", "a1", "u2", "a2"])
        await ChatService().fold_conversation(scope_id=_SCOPE[1], from_message_id=ids[0])
        with pytest.raises(ValueError, match="已在折叠段"):
            await ChatService().fold_conversation(scope_id=_SCOPE[1], from_message_id=ids[1])

    async def test_summary_fallback_when_llm_fails(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch, summarize="")
        ids = await _seed(sqlite, ["u1", "a1", "u2"])
        result = await ChatService().fold_conversation(
            scope_id=_SCOPE[1], from_message_id=ids[0])
        assert result["summary"].startswith("共 2 条消息")

    async def test_restore_via_service(self, sqlite, monkeypatch) -> None:
        from services.chat import ChatService
        self._runtime(sqlite, monkeypatch)
        ids = await _seed(sqlite, ["u1", "a1"])
        result = await ChatService().fold_conversation(
            scope_id=_SCOPE[1], from_message_id=ids[0])
        assert await ChatService().restore_conversation_fold(result["fold_id"]) is True
        assert await sqlite.list_conversation_folds(*_SCOPE) == []
