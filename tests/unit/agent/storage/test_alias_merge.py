"""别名实体跨频道历史合并（alias_merge_history）单元测试。"""

from __future__ import annotations

import pytest

from agent.messages.presets import MessageUser
from agent.storage.data_center import ConversationData
from agent.storage.storage_router import StorageDomain, StorageRouter


@pytest.fixture
async def conv(sqlite):
    """临时库 ConversationData（连接随 sqlite 基座关闭）。"""
    yield sqlite, ConversationData(StorageRouter(sqlite=sqlite), max_size=30)


class TestAliasMergedHistory:
    async def test_alias_history_merged_by_ts(self, conv) -> None:
        """同一人的 QQ 与 WebUI 历史按 ts 归并读取（别名关联后）。"""
        sqlite, cd = conv
        await _append(cd, "user", "qq:123", "QQ 消息", ts_ns=100)
        await _append(cd, "user", "webui:123", "WebUI 消息", ts_ns=200)

        # webui:123 → primary qq:123
        await sqlite.set_alias(
            scope_type="user", scope_id="webui:123",
            primary_scope_type="user", primary_scope_id="qq:123",
        )

        rows = await cd.get_conversation_record_by_everything(
            MessageUser(uid="123", adapter_key="webui")
        )
        contents = [r["content"] for r in rows]
        assert contents == ["QQ 消息", "WebUI 消息"], "两个频道的历史应按 ts 归并"

    async def test_no_alias_returns_own_history(self, conv) -> None:
        sqlite, cd = conv
        await _append(cd, "user", "qq:123", "QQ 消息", ts_ns=100)
        await _append(cd, "user", "webui:123", "WebUI 消息", ts_ns=200)

        rows = await cd.get_conversation_record_by_everything(
            MessageUser(uid="123", adapter_key="webui")
        )
        contents = [r["content"] for r in rows]
        assert contents == ["WebUI 消息"], "无别名时只读自身频道历史"

    async def test_fetch_conversation_multi_single_scope(self, conv) -> None:
        sqlite, _cd = conv
        await sqlite.append_conversation(
            scope_type="user", scope_id="qq:123", role="user", content="hi", ts_ns=1,
        )
        rows = await sqlite.fetch_conversation_multi(scopes=[("user", "qq:123")], limit=10)
        assert len(rows) == 1 and rows[0]["content"] == "hi"


async def _append(conv: ConversationData, scope_type: str, scope_id: str,
                  content: str, ts_ns: int) -> None:
    await conv.router.append(
        StorageDomain.CONVERSATION,
        scope_type=scope_type, scope_id=scope_id,
        role="user", content=content, ts_ns=ts_ns,
    )
