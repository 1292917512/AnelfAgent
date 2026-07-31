"""实体身份键（identity_scope）与会话键（entity_scope）的语义分离测试。

回归场景（生产 bug）：群消息触发 _handle_user_message → get_anything(群号, uid)
→ add_anything 必须按 user 域键存储，get_anything 返回处按同键命中——
否则 KeyError 'user_qq:1292917512'（身份键被误用会话语义 group_qq:群号）。
"""

from __future__ import annotations

import pytest

from agent.messages.characters import EntityData
from agent.storage.data_center import EverythingData
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter


class TestIdentityScope:
    def test_user_with_group_context_is_user_domain(self) -> None:
        """带群上下文的用户实体：身份键仍为 user 域（与会话键 group 域分离）。"""
        entity = EntityData(uid="1292917512", group_id="390320020", adapter_key="qq")
        assert entity.entity_scope == "group_qq:390320020"  # 会话语义：归群
        assert entity.identity_scope == "user_qq:1292917512"  # 身份语义：归人
        assert entity.identity_parts == ("user", "qq:1292917512")

    def test_group_entity_is_group_domain(self) -> None:
        entity = EntityData(uid=0, group_id="390320020", adapter_key="qq")
        assert entity.identity_scope == "group_qq:390320020"
        assert entity.identity_parts == ("group", "qq:390320020")

    def test_private_user_identity(self) -> None:
        entity = EntityData(uid="123", adapter_key="qq")
        assert entity.identity_scope == "user_qq:123"


@pytest.fixture
async def everything_data(tmp_path):
    sqlite = SqliteBackend(db_path=str(tmp_path / "agent.sqlite3"))
    yield EverythingData(StorageRouter(sqlite=sqlite))
    await sqlite.close()


class TestEverythingDataRoundTrip:
    async def test_group_message_user_roundtrip(self, everything_data) -> None:
        """群消息场景：get_anything(群号, uid) 两次调用命中同一 user 域键。"""
        first = await everything_data.get_anything(390320020, 1292917512, "qq")
        # 第二次调用走缓存分支（user_key in entities）——bug 时此处 KeyError
        second = await everything_data.get_anything(390320020, 1292917512, "qq")
        assert first is second
        assert "user_qq:1292917512" in everything_data.entities

    async def test_user_and_group_entities_coexist(self, everything_data) -> None:
        """同群的群实体与用户实体各占各的键，互不覆盖。"""
        user = await everything_data.get_anything(390320020, 1292917512, "qq")
        group = await everything_data.get_anything(390320020, 0, "qq")
        assert user.identity_scope == "user_qq:1292917512"
        assert group.identity_scope == "group_qq:390320020"
        assert len(everything_data.entities) == 2

    async def test_cross_channel_same_uid_separate_entities(self, everything_data) -> None:
        """跨频道同号用户：adapter 不同 → 不同实体（隔离目标）。"""
        qq_user = await everything_data.get_anything(0, 123, "qq")
        web_user = await everything_data.get_anything(0, 123, "webui")
        assert qq_user is not web_user
        assert "user_qq:123" in everything_data.entities
        assert "user_webui:123" in everything_data.entities

    async def test_counters_saved_to_identity_scope(self, everything_data, tmp_path) -> None:
        """群内用户的画像与计数写入 user 域键（不误写群键）。"""
        from agent.storage.storage_router import StorageDomain

        entity = await everything_data.get_anything(390320020, 1292917512, "qq")
        entity.personality["personality"] = "活跃用户"
        entity.personality["conv_num"] = 3
        entity.personality["conv_update_num"] = 3
        await everything_data.save_entity_personality(entity)

        # 画像落在 user 域键
        saved = await everything_data.router.get_one(
            StorageDomain.ENTITY_PROFILE,
            scope_type="user", scope_id="qq:1292917512",
        )
        assert saved is not None
        assert saved.get("personality") == "活跃用户"
        assert saved.get("conv_num") == 3

        # 群键上不应出现该用户的画像
        wrong = await everything_data.router.get_one(
            StorageDomain.ENTITY_PROFILE,
            scope_type="group", scope_id="qq:1292917512",
        )
        assert wrong is None
