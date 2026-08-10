from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Union

from agent.messages import EntityData, Everything, build_entity_scope, build_scope_id
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageDomain, StorageRouter
from core.entity import EntityMetadata, EntityRegistry, EntityType
from core.log import log

MaxConversationSize = 30


class EverythingData:
    """维护运行时在线实体画像（人/群）。"""

    def __init__(self, router: StorageRouter) -> None:
        self.router = router
        self.entities: dict[str, EntityData] = {}
        # 按 scope_key 隔离的加载锁：避免并发 get_anything 对同一 scope 重复加载创建
        self._load_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, scope_key: str) -> asyncio.Lock:
        """获取/创建指定 scope 的加载锁（同步操作，单线程事件循环下原子）。"""
        lock = self._load_locks.get(scope_key)
        if lock is None:
            lock = asyncio.Lock()
            self._load_locks[scope_key] = lock
        return lock

    def add_anything(self, entity: EntityData) -> None:
        # 内存实体键用身份语义（用户实体恒 user 域），与会话键（群消息归群）区分
        self.entities[entity.identity_scope] = entity

    async def get_anything(
        self,
        group_id: Union[int, str] = 0,
        uid: Union[int, str] = 0,
        adapter_key: str = "",
    ) -> EntityData:
        if uid not in (0, "0", None):
            user_key = build_entity_scope("user", adapter_key, str(uid))
            async with self._lock_for(user_key):
                if user_key not in self.entities:
                    entity = EntityData(uid=uid, group_id=group_id, adapter_key=adapter_key)
                    # 先加载自身 scope 的计数，再通过 alias 加载 primary 的画像
                    own_data = await self.router.get_one(
                        StorageDomain.ENTITY_PROFILE,
                        scope_type="user",
                        scope_id=build_scope_id(adapter_key, str(uid)),
                    )
                    primary_data = await self._load_primary_profile(
                        "user", build_scope_id(adapter_key, str(uid))
                    )
                    self._restore_entity_with_alias(entity, own_data, primary_data)
                    self.add_anything(entity)
        group_key = build_entity_scope("group", adapter_key, str(group_id))
        async with self._lock_for(group_key):
            if group_key not in self.entities:
                entity = EntityData(uid=0, group_id=group_id, adapter_key=adapter_key)
                own_data = await self.router.get_one(
                    StorageDomain.ENTITY_PROFILE,
                    scope_type="group",
                    scope_id=build_scope_id(adapter_key, str(group_id)),
                )
                primary_data = await self._load_primary_profile(
                    "group", build_scope_id(adapter_key, str(group_id))
                )
                self._restore_entity_with_alias(entity, own_data, primary_data)
                self.add_anything(entity)

        if uid not in (0, "0", None):
            return self.entities[build_entity_scope("user", adapter_key, str(uid))]
        return self.entities[group_key]

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

        scope_type, scope_id = entity.identity_parts

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
        scope_type, scope_id = entity.identity_parts
        await self.router.sqlite.save_entity_counters(
            scope_type=scope_type, scope_id=scope_id,
            conv_num=conv_num, conv_update_num=conv_update_num,
        )

    async def save_all_entity_counters(self) -> int:
        """批量持久化所有在线实体的对话计数（单事务一次提交），返回保存数量。"""
        records: list[tuple[str, str, int, int]] = []
        for entity in self.entities.values():
            conv_num = int(entity.personality.get("conv_num", 0))
            if conv_num <= 0:
                continue
            conv_update_num = int(entity.personality.get("conv_update_num", 0))
            scope_type, scope_id = entity.identity_parts
            records.append((scope_type, scope_id, conv_num, conv_update_num))
        return await self.router.sqlite.save_entity_counters_batch(records)


class ConversationData:
    """会话记录（通过 StorageRouter 写入 SQLite）。"""

    def __init__(self, router: StorageRouter, max_size: Optional[int] = None) -> None:
        self.router = router
        # 显式传入时固定（测试/定制）；None 时每次实时读配置（Web 调整即时生效）
        self._max_size_override = max_size
        # scope_key("user_123"/"group_456") → 最近一次历史快照的最大 ts_ns（快照水位）。
        # 水位与快照出自同一次 SELECT，think_loop 以此增量合并循环期间新消息。
        self._fetch_watermarks: dict[str, int] = {}
        # id 水位：与 ts 水位同次快照记录，同 ts_ns 并列的消息靠 id 精确决胜
        self._fetch_watermark_ids: dict[str, int] = {}

    @property
    def max_size(self) -> int:
        """对话窗口上限 M（未显式覆盖时实时读 ConfigManager，Web 调整立即生效）。"""
        if self._max_size_override is not None:
            return self._max_size_override
        try:
            from core.config import ConfigManager
            val = ConfigManager.get("max_conversation_size")
            if val is not None:
                return int(val)
        except Exception:
            log("max_conversation_size 配置读取失败，使用默认值", "DEBUG")
        return MaxConversationSize

    async def get_conversation_record_by_everything(self, anything: Everything) -> list[dict]:
        scope_type, scope_id = self._scope_of(anything)
        scopes = await self._alias_merged_scopes(scope_type, scope_id)
        rows = await self._fetch_window_rows(scope_type, scope_id, scopes)
        # 记录快照水位（快照内最大 ts_ns 与最大 id），并剥离 ts_ns/id 避免泄漏进 LLM 消息
        max_ts = 0
        max_id = 0
        records: list[dict] = []
        for row in rows:
            ts = int(row.get("ts_ns", 0) or 0)
            if ts > max_ts:
                max_ts = ts
            rid = int(row.get("id", 0) or 0)
            if rid > max_id:
                max_id = rid
            records.append({"role": row["role"], "content": row["content"]})
        self._fetch_watermarks[f"{scope_type}_{scope_id}"] = max_ts
        self._fetch_watermark_ids[f"{scope_type}_{scope_id}"] = max_id
        return records

    async def _fetch_window_rows(
        self,
        scope_type: str,
        scope_id: str,
        scopes: list[tuple[str, str]],
    ) -> list[dict]:
        """摘要窗口取数：水位线后原始消息（纯追加增长），到 M+H 触发后台折叠。

        窗口在 x（保留比例派生）与 M+x 之间波动：到达 M+x（滞回 H=x，
        每批折 M 条）时触发折叠，折叠在途允许宽限到 M+2x
        （保持前缀只增不改）；折叠失败默认丢弃该批并推进水位线
        （conversation_fold_drop_on_failure），窗口头部永不逐条滑动；
        仅连续异常导致超出宽限时才硬降级为"最后 M 条滑动"。
        """
        from agent.storage.conversation_fold import (
            conversation_folder,
            fold_hysteresis,
            is_summary_enabled,
            raw_min_messages,
        )
        sqlite = self.router.sqlite
        if not is_summary_enabled():
            return await sqlite.fetch_conversation_multi(scopes=scopes, limit=self.max_size)

        summary_row = await sqlite.get_conversation_summary(
            scope_type=scope_type, scope_id=scope_id,
        )
        watermarks = (summary_row or {}).get("watermarks", {})
        watermark_ids = (summary_row or {}).get("watermark_ids", {})
        raw_min = min(raw_min_messages(), self.max_size - 1)
        trigger = self.max_size + fold_hysteresis()
        # 多取 raw_min+1 条：≤ trigger+x 视为折叠在途宽限（保持追加语义），
        # 超出说明连续异常（折叠失败且未丢批），硬降级为最后 M 条滑动
        grace = trigger + raw_min
        rows = await sqlite.fetch_conversation_after_watermarks(
            scopes=scopes, watermarks=watermarks, limit=grace + 1,
            watermark_ids=watermark_ids,
        )
        # 折叠调度判定必须用截断前的行数：若先截断到 M 再判定，
        # 积压一旦超过宽限（折叠在途/失败/重启期间消息继续到达），
        # 截断后恒 < trigger 永不调度——水位线停滞、窗口逐条滑动、
        # 缓存前缀每条消息断裂的永久死态（曾致折叠停摆两天）
        needs_fold = len(rows) >= trigger
        if len(rows) > grace:
            rows = rows[-self.max_size:]
        if needs_fold:
            conversation_folder.maybe_schedule_fold(
                self, scope_type, scope_id, scopes, watermarks, watermark_ids,
            )
        return rows

    async def get_conversation_summary(self, anything: Everything) -> Optional[dict]:
        """读取该 scope 的对话摘要行（{summary, watermarks, folded_count}），未生成返回 None。"""
        from agent.storage.conversation_fold import is_summary_enabled
        if not is_summary_enabled():
            return None
        scope_type, scope_id = self._scope_of(anything)
        try:
            return await self.router.sqlite.get_conversation_summary(
                scope_type=scope_type, scope_id=scope_id,
            )
        except Exception as exc:
            from core.log import log
            log(f"对话摘要读取失败: {exc}", "DEBUG", tag="存储")
            return None

    async def _alias_merged_scopes(self, scope_type: str, scope_id: str) -> list[tuple[str, str]]:
        """解析别名关联，返回 (primary + 全部 alias) 的 scope 列表（当前 scope 在前）。

        未启用或无别名时返回仅含自身的单元素列表。
        """
        try:
            from agent.storage.scope_migrate import is_alias_merge_enabled
            if not is_alias_merge_enabled():
                return [(scope_type, scope_id)]
            sqlite = self.router.sqlite
            primary = await sqlite.resolve_alias(scope_type, scope_id)
            p_type, p_id = primary if primary else (scope_type, scope_id)
            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            merged = [(p_type, p_id)] + [
                (str(a["scope_type"]), str(a["scope_id"])) for a in aliases
            ]
            current = (scope_type, scope_id)
            ordered = [current] + [s for s in merged if s != current]
            # 去重保持顺序
            seen: set[tuple[str, str]] = set()
            result: list[tuple[str, str]] = []
            for s in ordered:
                if s not in seen:
                    seen.add(s)
                    result.append(s)
            return result
        except Exception:
            return [(scope_type, scope_id)]

    # ------------------------------------------------------------------
    # 折叠调度（空闲自动折叠 / AI 主动整理共用入口）
    # ------------------------------------------------------------------

    @property
    def fold_idle_min(self) -> int:
        """空闲折叠阈值（派生自窗口参数：窗口上限 M − 折叠后保留 x）。

        语义：窗口积满到 M 即达到"应该折叠"的大小，空闲期到 M−x 就值得折
        （折完回到 x，与活跃期滞回触发错开）。不单独配置，随窗口参数联动。
        """
        from agent.storage.conversation_fold import raw_min_messages
        return max(1, self.max_size - raw_min_messages())

    async def list_scope_activity(self) -> list[tuple[str, str, int]]:
        """列出全部会话 scope 及最新外部消息 ts_ns（空闲折叠扫描用）。"""
        return await self.router.sqlite.list_scope_activity()

    async def scope_backlog(self, scope_type: str, scope_id: str) -> int:
        """该 scope 水位线后的未折叠消息数（别名合并口径）。"""
        scopes = await self._alias_merged_scopes(scope_type, scope_id)
        summary_row = await self.router.sqlite.get_conversation_summary(
            scope_type=scope_type, scope_id=scope_id,
        )
        return await self.router.sqlite.count_after_watermarks(
            scopes=scopes,
            watermarks=(summary_row or {}).get("watermarks", {}),
            watermark_ids=(summary_row or {}).get("watermark_ids", {}),
        )

    async def schedule_fold(self, scope_type: str, scope_id: str) -> bool:
        """调度一次后台折叠（别名合并 + 水位线解析在此收敛）；已在折叠/退避中返回 False。"""
        from agent.storage.conversation_fold import conversation_folder
        scopes = await self._alias_merged_scopes(scope_type, scope_id)
        summary_row = await self.router.sqlite.get_conversation_summary(
            scope_type=scope_type, scope_id=scope_id,
        )
        return conversation_folder.maybe_schedule_fold(
            self, scope_type, scope_id, scopes,
            (summary_row or {}).get("watermarks", {}),
            (summary_row or {}).get("watermark_ids", {}),
        )

    def get_fetch_watermark(self, scope_type: str, scope_id: str) -> Optional[int]:
        """返回指定 scope 最近一次历史快照的水位（最大 ts_ns），未快照过返回 None。"""
        return self._fetch_watermarks.get(f"{scope_type}_{scope_id}")

    def get_fetch_watermark_id(self, scope_type: str, scope_id: str) -> int:
        """返回指定 scope 最近一次历史快照的 id 水位（最大行 id），未快照过返回 0。"""
        return self._fetch_watermark_ids.get(f"{scope_type}_{scope_id}", 0)

    async def count_messages(self, anything: Everything) -> int:
        """该 scope 的对话消息总数（含窗口外历史，溢出提示感知用）。"""
        scope_type, scope_id = self._scope_of(anything)
        return await self.router.sqlite.count_conversation(
            scope_type=scope_type, scope_id=scope_id,
        )

    @staticmethod
    def _scope_of(anything: Everything) -> tuple[str, str]:
        """会话持久化键：与 entity_scope 同源（含子会话 #chat_id 后缀）。"""
        return anything.scope_type, anything.scope_id

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
                file_id = getattr(seg, "file_id", "")
                if seg_type and (file_path or file_id):
                    type_name = seg_type.value if hasattr(seg_type, "value") else str(seg_type)
                    line = f"[media_type:{type_name}]"
                    if file_path:
                        line += f"[media_path:{file_path}]"
                    else:
                        line += "[media_path:未下载]"
                    if file_id:
                        line += f"[media_file_id:{file_id}]"
                    media_lines.append(line)

        if media_lines:
            content = content + "\n" + "\n".join(media_lines)

        scope_type, scope_id = self._scope_of(anything)
        # 以消息到达时间入库，保证对话历史严格按到达时序排列；
        # adapter_key 记录来源频道，供启动时未回复恢复定位回复路由；
        # trigger_mind 记录消息当时是否触发思考（非 @ 群消息记 False），
        # 供启动恢复扫描排除"本就不该回复"的消息
        await self.router.append(
            StorageDomain.CONVERSATION,
            scope_type=scope_type, scope_id=scope_id,
            role=role, content=content,
            ts_ns=anything.created_ts_ns,
            adapter_key=getattr(anything, "adapter_key", "") or "",
            trigger_mind=bool(getattr(anything, "trigger_mind", True)),
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

    dc = DataCenter(
        sqlite=sqlite,
        router=router,
        everything_data=EverythingData(router),
        # max_size 不传 → ConversationData 每次实时读配置（Web 调整窗口即时生效）
        conversation_data=ConversationData(router),
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
