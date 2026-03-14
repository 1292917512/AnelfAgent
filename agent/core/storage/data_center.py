from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from agent.core.messages import EntityData, Everything, EverythingGroup
from agent.core.storage.sqlite_backend import SqliteBackend
from agent.core.storage.storage_router import StorageDomain, StorageRouter
from core.entity import EntityMetadata, EntityRegistry, EntityType

MaxConversationSize = 30


class EverythingData:
    """维护运行时在线实体画像（人/群）。"""

    def __init__(self, router: StorageRouter) -> None:
        self.router = router
        self.entities: dict[str, EntityData] = {}

    def add_anything(self, entity: EntityData) -> None:
        if entity.uid not in (0, "0", None):
            self.entities[f"user_{entity.uid}"] = entity
        else:
            self.entities[f"group_{entity.group_id}"] = entity

    async def get_anything(self, group_id: Union[int, str] = 0, uid: Union[int, str] = 0) -> EntityData:
        if uid not in (0, "0", None) and f"user_{uid}" not in self.entities:
            entity = EntityData(uid=uid, group_id=group_id)
            # 先加载自身 scope 的计数，再通过 alias 加载 primary 的画像
            own_data = await self.router.get_one(
                StorageDomain.ENTITY_PROFILE, scope_type="user", scope_id=str(uid)
            )
            primary_data = await self._load_primary_profile("user", str(uid))
            self._restore_entity_with_alias(entity, own_data, primary_data)
            self.add_anything(entity)
        if f"group_{group_id}" not in self.entities:
            entity = EntityData(uid=0, group_id=group_id)
            own_data = await self.router.get_one(
                StorageDomain.ENTITY_PROFILE, scope_type="group", scope_id=str(group_id)
            )
            primary_data = await self._load_primary_profile("group", str(group_id))
            self._restore_entity_with_alias(entity, own_data, primary_data)
            self.add_anything(entity)

        if uid not in (0, "0", None):
            return self.entities[f"user_{uid}"]
        return self.entities[f"group_{group_id}"]

    async def _load_primary_profile(self, scope_type: str, scope_id: str) -> Optional[dict]:
        """若存在 alias 映射，加载 primary 的画像数据。"""
        primary = await self.router.sqlite.resolve_alias(scope_type, scope_id)
        if not primary:
            return None
        return await self.router.get_one(
            StorageDomain.ENTITY_PROFILE,
            scope_type=primary[0], scope_id=primary[1],
        )

    @staticmethod
    def _restore_entity_with_alias(
        entity: EntityData,
        own_data: Optional[dict],
        primary_data: Optional[dict],
    ) -> None:
        """恢复实体：画像取 primary（若有 alias），计数取自身。"""
        # 先恢复自身数据（含计数）
        EverythingData._restore_entity_from_db(entity, own_data)
        # 若有 primary alias，用 primary 的画像覆盖（保留自身计数）
        if primary_data:
            personality = (
                primary_data.get("personality")
                if isinstance(primary_data, dict) else primary_data
            )
            if personality:
                entity.personality["personality"] = personality

    @staticmethod
    def _restore_entity_from_db(entity: EntityData, data: Optional[dict]) -> None:
        """从 SQLite 返回的 dict 恢复 personality 和对话计数。"""
        if not data:
            return
        if isinstance(data, str):
            entity.personality["personality"] = data
            return
        if data.get("personality"):
            entity.personality["personality"] = data["personality"]
        if data.get("conv_num"):
            entity.personality["conv_num"] = data["conv_num"]
        if data.get("conv_update_num"):
            entity.personality["conv_update_num"] = data["conv_update_num"]

    def get_everything_data(self) -> list[dict]:
        everything_data_list: list[dict] = []
        for entity in self.entities.values():
            if desc := entity.get_personality_desc():
                everything_data_list.append(desc)
        return everything_data_list

    async def resolve_primary_scope(self, scope_type: str, scope_id: str) -> tuple[str, str]:
        """解析 alias，返回 (primary_type, primary_id)；无别名时返回原值。"""
        primary = await self.router.sqlite.resolve_alias(scope_type, scope_id)
        return primary if primary else (scope_type, scope_id)

    async def save_entity_personality(self, entity: EntityData) -> None:
        """持久化实体画像（写入 primary scope）及自身对话计数。"""
        personality = entity.personality.get("personality")
        if not personality:
            return
        conv_num = int(entity.personality.get("conv_num", 0))
        conv_update_num = int(entity.personality.get("conv_update_num", 0))

        if entity.uid not in (0, "0", None):
            scope_type, scope_id = "user", str(entity.uid)
        else:
            scope_type, scope_id = "group", str(entity.group_id)

        # 画像写入 primary scope
        p_type, p_id = await self.resolve_primary_scope(scope_type, scope_id)
        await self.router.upsert(
            StorageDomain.ENTITY_PROFILE,
            scope_type=p_type, scope_id=p_id, personality=personality,
            conv_num=conv_num, conv_update_num=conv_update_num,
        )

    async def save_entity_counters(self, entity: EntityData) -> None:
        """仅持久化对话计数（不覆盖画像内容）。"""
        conv_num = int(entity.personality.get("conv_num", 0))
        conv_update_num = int(entity.personality.get("conv_update_num", 0))
        if entity.uid not in (0, "0", None):
            scope_type, scope_id = "user", str(entity.uid)
        else:
            scope_type, scope_id = "group", str(entity.group_id)
        await self.router.sqlite.save_entity_counters(
            scope_type=scope_type, scope_id=scope_id,
            conv_num=conv_num, conv_update_num=conv_update_num,
        )

    async def save_all_entity_counters(self) -> int:
        """批量持久化所有在线实体的对话计数，返回保存数量。"""
        count = 0
        for entity in self.entities.values():
            conv_num = int(entity.personality.get("conv_num", 0))
            if conv_num > 0:
                await self.save_entity_counters(entity)
                count += 1
        return count


class ConversationData:
    """会话记录（通过 StorageRouter 写入 SQLite）。"""

    def __init__(self, router: StorageRouter, max_size: int = MaxConversationSize) -> None:
        self.router = router
        self.max_size = max_size

    async def get_conversation_record_by_everything(self, anything: Everything) -> list[dict]:
        if isinstance(anything, EverythingGroup) and anything.group_id not in (0, "0", "", None):
            return await self.router.fetch(
                StorageDomain.CONVERSATION,
                scope_type="group", scope_id=str(anything.group_id), limit=self.max_size,
            )
        return await self.router.fetch(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id=str(anything.uid), limit=self.max_size,
        )

    async def search_conversation_vector(
        self,
        scope_type: str,
        scope_id: str,
        query_vec: list[float],
        *,
        limit: int = 5,
        skip_recent: int = 0,
        min_score: float = 0.25,
        scan_limit: int = 500,
    ) -> list[dict]:
        """向量搜索对话历史（委托给 SQLite 后端）。"""
        return await self.router.sqlite.search_conversation_vector(
            scope_type, scope_id, query_vec,
            limit=limit, skip_recent=skip_recent,
            min_score=min_score, scan_limit=scan_limit,
        )

    async def add_conversation_record_by_everything(self, anything: Everything) -> None:
        msg = anything.get_agent_dic()
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))

        # 将媒体文件路径以标签形式追加到 content 中
        # 使用 [media_type:xxx][media_path:yyy] 格式，避免路径中的冒号造成解析问题
        media_lines: list[str] = []

        # 图片
        if anything.images:
            for img in anything.images:
                path = img.data
                if path:
                    media_lines.append(f"[media_type:image][media_path:{path}]")

        # 其他媒体（语音、音频、视频、文件）
        if hasattr(anything, "media_segments") and anything.media_segments:
            for seg in anything.media_segments:
                seg_type = getattr(seg, "type", None)
                file_path = getattr(seg, "file_path", "") or getattr(seg, "url", "")
                if seg_type and file_path:
                    type_name = seg_type.value if hasattr(seg_type, "value") else str(seg_type)
                    media_lines.append(f"[media_type:{type_name}][media_path:{file_path}]")

        if media_lines:
            content = content + "\n" + "\n".join(media_lines)

        if isinstance(anything, EverythingGroup) and anything.group_id not in (0, "0", "", None):
            await self.router.append(
                StorageDomain.CONVERSATION,
                scope_type="group", scope_id=str(anything.group_id),
                role=role, content=content,
            )
        else:
            await self.router.append(
                StorageDomain.CONVERSATION,
                scope_type="user", scope_id=str(anything.uid),
                role=role, content=content,
            )


@dataclass(slots=True)
class DataCenter:
    """综合数据中心对象，便于注入。"""

    sqlite: SqliteBackend
    router: StorageRouter
    everything_data: EverythingData
    conversation_data: ConversationData


def create_data_center(
    sqlite: Optional[SqliteBackend] = None,
) -> DataCenter:
    sqlite = sqlite or SqliteBackend()
    router = StorageRouter(sqlite=sqlite)

    max_conv = MaxConversationSize
    try:
        from agent.core.config import get_config_provider
        max_conv = get_config_provider().config.max_conversation_size
    except Exception as e:
        from core.log import log
        log(f"会话大小配置加载失败，使用默认值 {max_conv}: {e}", "DEBUG")

    dc = DataCenter(
        sqlite=sqlite,
        router=router,
        everything_data=EverythingData(router),
        conversation_data=ConversationData(router, max_size=max_conv),
    )

    EntityRegistry.register(EntityMetadata(
        name="data_center",
        entity_type=EntityType.STORAGE,
        description="Data storage hub - SQLite, conversations, entity profiles",
        enabled=True,
        instance=dc,
        source="builtin",
    ))

    return dc
