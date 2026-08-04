"""WorkMemory — 工作记忆的"数据面"：消息队列、待办持久化、短期记忆、媒体暂存与态势路由。

从 PrefrontalCortex 拆分而来（原 PFC 职责过载，见 agent/mind/prefrontal_cortex.py）。
本模块为自包含状态类，不依赖 Mind；工具激活委托给 ToolAssembly（构造后由门面接线）。

职责：
- 消息队列管理（pending_user / pending_group / pending_analysis，按 scope 分桶隔离）
- 待办跨重启持久化（pending_tasks 表双写 + 启动 replay）
- 通用任务队列（错误反馈、AI 自主任务、画像分析等）
- 短期记忆（_temporary，按 scope 分桶）
- 待处理媒体暂存与 scope → adapter 路由表 / 未读计数 / 消息预览
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from agent.llm.types import ImageContent
from agent.messages import EntityData, Everything, EverythingGroup, build_entity_scope
from agent.mind.autonomous import MindTask, TaskType
from agent.storage.data_center import EverythingData
from agent.utils.unique_queue import UniqueQueue
from core.log import log
from core.tags import etag_all

if TYPE_CHECKING:
    from agent.mind.tool_assembly import ToolAssembly
    from agent.storage.data_center import ConversationData
    from agent.storage.sqlite_backend import SqliteBackend


def _pfc_persist_enabled() -> bool:
    """PFC 待办跨重启持久化开关（配置 pfc_persist_enabled，默认开）。"""
    try:
        from core.config import get_config_bool
        return get_config_bool("pfc_persist_enabled", True)
    except Exception:
        return True


class WorkMemory:
    """消息队列 + 短期记忆 + 态势路由（PFC 数据面组件）。"""

    def __init__(
            self,
            everything_data: EverythingData,
            conversation_data: Optional["ConversationData"] = None,
    ) -> None:
        # 短期记忆按 scope 分桶（跨会话隔离，避免并行会话互相串内容）；
        # 无 scope 的写入进 "_default" 全局桶，读取时与本 scope 桶合并。
        self._temporary: dict[str, list[Dict]] = {}
        self.everything_data = everything_data
        self._conversation_data = conversation_data
        # 工具激活接线（门面注入）：消息标签扫描命中时激活对应工具
        self.tool_assembly: Optional["ToolAssembly"] = None

        # 消息任务队列（元素为 entity_scope 字符串，如 user_qq:123 / user_webui:u#chat_id）
        self.pending_user: UniqueQueue[str] = UniqueQueue()
        self.pending_group: UniqueQueue[str] = UniqueQueue()
        self.pending_analysis: UniqueQueue[tuple[Union[int, str], Union[int, str], str]] = UniqueQueue()
        # 按 scope 分桶的待处理媒体（跨频道并行不串台）
        self._pending_images: dict[str, List[ImageContent]] = {}
        self._pending_media: dict[str, list] = {}

        # scope → 消息预览 / adapter_key 路由 / 未读计数
        self._message_previews: dict[str, str] = {}
        self._task_adapter_keys: dict[str, str] = {}
        self._unread_counts: dict[str, int] = {}
        # 群聊 scope → 最近发送者 [(uid, name), ...]
        self._group_recent_senders: dict[str, list[tuple[str, str]]] = {}

        # 通用任务（错误反馈、AI 自主任务、画像分析等）
        self._general_tasks: list[MindTask] = []
        # 与 _general_tasks 对齐的持久化 task_key（双写 pending_tasks 表用）
        self._general_task_keys: list[str] = []
        # pending_analysis 条目 (group_id, uid, adapter_key) → 持久化 task_key
        self._analysis_task_keys: dict[tuple, str] = {}

    # ==================================================================
    # 待办持久化（pending_tasks 表双写，崩溃重启后 replay）
    # ==================================================================

    def _persist_db(self) -> Optional["SqliteBackend"]:
        cd = self._conversation_data
        if cd is None:
            return None
        try:
            return cd.router.sqlite
        except Exception:
            return None

    def _persist_add(self, *, scope: str, kind: str, payload: Dict[str, Any]) -> None:
        """异步双写待办（fire-and-forget；失败仅记日志，不阻塞主流程）。"""
        if not _pfc_persist_enabled():
            return
        db = self._persist_db()
        if db is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环（同步/测试上下文），跳过持久化

        async def _add() -> None:
            try:
                await db.add_pending_task(
                    scope=scope, kind=kind,
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                )
            except Exception as exc:
                log(f"待办持久化写入失败: {exc}", "DEBUG", tag="PFC")

        loop.create_task(_add())

    def _persist_remove(self, task_key: str) -> None:
        """异步删除已消费待办（fire-and-forget）。"""
        if not task_key or not _pfc_persist_enabled():
            return
        db = self._persist_db()
        if db is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _del() -> None:
            try:
                await db.delete_pending_task_by_key(task_key)
            except Exception as exc:
                log(f"待办持久化删除失败: {exc}", "DEBUG", tag="PFC")

        loop.create_task(_del())

    def restore_persisted_tasks(self, rows: List[Dict[str, Any]]) -> int:
        """启动时 replay 持久化的待办（bootstrap 调用，同步重建内存队列）。

        复用 payload 中的 task_key：消费时按 key 删除对应行，
        行在消费前保留，replay 后再次崩溃也不会丢。
        """
        restored = 0
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            key = str(payload.get("task_key") or "")
            kind = row.get("kind", "")
            if kind == "analysis":
                entry = (
                    payload.get("group_id", 0),
                    payload.get("uid", 0),
                    str(payload.get("adapter_key", "") or ""),
                )
                self.pending_analysis.append(entry)
                if key:
                    self._analysis_task_keys[entry] = key
                restored += 1
            elif kind == "general":
                try:
                    task = MindTask(
                        task_type=TaskType(payload.get("task_type", "self_task")),
                        scope=payload.get("scope", ""),
                        preview=payload.get("preview", ""),
                        adapter_key=payload.get("adapter_key", ""),
                        uid=payload.get("uid", 0),
                        group_id=payload.get("group_id", 0),
                        timestamp=payload.get("timestamp", 0.0) or 0.0,
                        metadata=payload.get("metadata", {}) or {},
                    )
                except Exception:
                    continue
                self._general_tasks.append(task)
                self._general_task_keys.append(key)
                restored += 1
        return restored

    # ==================================================================
    # 短期记忆
    # ==================================================================

    @property
    def _max_temp(self) -> int:
        from agent.config import get_mind_config
        return get_mind_config().short_term_memory_size

    @property
    def max_temp(self) -> int:
        """短期记忆桶容量上限（监控/执行上下文展示用）。"""
        return self._max_temp

    def get_group_recent_senders(self, scope: str) -> list[tuple[str, str]]:
        """群聊 scope 的最近发送者列表 [(uid, name), ...]（场景信息用）。"""
        return self._group_recent_senders.get(scope, [])

    @property
    def temporary(self) -> list[Dict]:
        """全桶展开视图（管理接口/监控用；LLM 上下文请用 get_temporary(scope)）。"""
        result: list[Dict] = []
        for bucket in self._temporary.values():
            result.extend(bucket)
        return result

    def get_temporary(self, scope: str = "") -> list[Dict]:
        """指定 scope 的短期记忆（_default 全局桶 + 本 scope 桶）。"""
        result = list(self._temporary.get("_default", []))
        if scope and scope != "_default":
            result.extend(self._temporary.get(scope, []))
        return result

    def add_temporary(self, temporary_clip: Dict, scope: str = "") -> None:
        """写入短期记忆到指定 scope 桶（空 scope 进 _default 全局桶）。

        溢出晋升：被容量截尾挤出的最老条目追加到当天 events 日期便签，
        可被文件索引检索，避免静默丢失。
        """
        key = scope or "_default"
        bucket = self._temporary.setdefault(key, [])
        bucket.append(temporary_clip)
        if len(bucket) > self._max_temp:
            overflowed = bucket[:-self._max_temp]
            self._temporary[key] = bucket[-self._max_temp:]
            self._promote_overflow(overflowed, key)

    @staticmethod
    def _promote_overflow(clips: List[Dict], bucket_key: str) -> None:
        """将溢出的短期记忆条目追加到当天 events 日期便签（尽力而为，失败仅记日志）。"""
        if not clips:
            return
        try:
            from agent.memory.notes import append_to_memory_file
            today = time.strftime("%Y-%m-%d")
            now = time.strftime("%H:%M")
            lines = [f"\n## 短期记忆溢出（{now}，桶 {bucket_key}）\n"]
            for clip in clips:
                content = str(clip.get("content", "")).strip().replace("\n", " ")[:500]
                if content:
                    lines.append(f"- {content}")
            if len(lines) > 1:
                append_to_memory_file(f"memory/events/{today}.md", "\n".join(lines) + "\n")
        except Exception as exc:
            log(f"短期记忆溢出晋升失败: {exc}", "DEBUG", tag="思维")

    def delete_temporary(self, index: int) -> bool:
        """按全桶展开视图的索引删除一条短期记忆。"""
        if index < 0:
            return False
        offset = index
        for key in list(self._temporary.keys()):
            bucket = self._temporary[key]
            if offset < len(bucket):
                bucket.pop(offset)
                if not bucket:
                    del self._temporary[key]
                return True
            offset -= len(bucket)
        return False

    def clear_temporary(self) -> int:
        count = sum(len(bucket) for bucket in self._temporary.values())
        self._temporary.clear()
        return count

    # ==================================================================
    # 消息入队 / 画像分析触发
    # ==================================================================

    async def add_task(self, anything: Everything) -> None:
        """将消息加入待处理队列，收集附带媒体，触发画像分析检查。"""
        scope = anything.entity_scope
        if anything.images:
            self._pending_images.setdefault(scope, []).extend(anything.images)
        if hasattr(anything, "media_segments") and anything.media_segments:
            self._pending_media.setdefault(scope, []).extend(anything.media_segments)

        preview = anything.get_text_content()[:300] if hasattr(anything, "get_text_content") else str(anything)[:300]
        adapter_key = getattr(anything, "adapter_key", "") or ""

        self._unread_counts[scope] = self._unread_counts.get(scope, 0) + 1
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            self.pending_group.append(scope)
            self._message_previews[scope] = preview
            if adapter_key:
                self._task_adapter_keys[scope] = adapter_key
            uid = str(anything.uid) if anything.uid and anything.uid not in (0, "0") else ""
            name = getattr(anything, "user_name", "") or getattr(anything, "nickname", "") or ""
            if uid:
                senders = self._group_recent_senders.setdefault(scope, [])
                entry = (uid, name)
                if entry not in senders:
                    senders.append(entry)
                if len(senders) > 10:
                    senders[:] = senders[-10:]
            await self._handle_group_message(anything)
        else:
            self.pending_user.append(scope)
            self._message_previews[scope] = preview
            if adapter_key:
                self._task_adapter_keys[scope] = adapter_key

        await self._handle_user_message(anything)
        self._scan_message_tags(str(anything))

    def _scan_message_tags(self, content: str) -> None:
        """扫描消息中的标签，按 key 和 value 搜索匹配工具。

        [media_type:image][media_path:path] -> tag "media:image"
        [media_file:image:path]             -> tag "media:image"
        [channel:telegram]                  -> tag "channel", "telegram"
        [platform:qq]                       -> tag "platform", "qq"
        """
        assembly = self.tool_assembly
        if assembly is None:
            return
        tags = etag_all(content)
        for key, value in tags:
            if key in ("media_type", "media_file"):
                # [media_type:image] 的 value 即媒体类型；[media_file:image:path] 取首段
                media_kind = value.split(":", 1)[0] if value else ""
                if media_kind:
                    assembly.activate_by_tag(f"media:{media_kind}")
            else:
                assembly.activate_by_tag(key)
                first_val = value.split(":")[0] if value else ""
                if first_val and first_val != key:
                    assembly.activate_by_tag(first_val)

    @staticmethod
    def _analysis_threshold() -> int:
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.conversation_analysis_threshold
        except Exception:
            return 5

    def _enqueue_analysis(
        self, group_id: Union[int, str], uid: Union[int, str], adapter_key: str = ""
    ) -> None:
        """画像分析任务入队（去重），并双写 pending_tasks 表。"""
        entry = (group_id, uid, adapter_key)
        if entry in self.pending_analysis.seen:
            return
        self.pending_analysis.append(entry)
        key = uuid.uuid4().hex
        self._analysis_task_keys[entry] = key
        if group_id not in (0, "0", "", None):
            scope = build_entity_scope("group", adapter_key, str(group_id))
        else:
            scope = build_entity_scope("user", adapter_key, str(uid))
        self._persist_add(scope=scope, kind="analysis", payload={
            "task_key": key, "group_id": group_id, "uid": uid, "adapter_key": adapter_key,
        })

    def requeue_analysis(
        self, group_id: Union[int, str], uid: Union[int, str], adapter_key: str = ""
    ) -> None:
        """分析未能执行时重新入队（去重由 _enqueue_analysis 保证）。"""
        self._enqueue_analysis(group_id, uid, adapter_key)

    async def _handle_group_message(self, anything: EverythingGroup) -> None:
        """群组消息达到阈值时加入画像分析队列，分析后重置计数实现周期性增量更新。"""
        adapter_key = str(getattr(anything, "adapter_key", "") or "")
        group_entity = await self.everything_data.get_anything(anything.group_id, 0, adapter_key)
        conv_count = group_entity.add_conversations_num()
        if conv_count >= self._analysis_threshold():
            self._enqueue_analysis(group_entity.group_id, group_entity.uid or 0, adapter_key)
            group_entity.reset_conversations_num()

    async def _handle_user_message(self, anything: Everything) -> None:
        """用户消息达到阈值时加入画像分析队列；首次出现的用户自动建档。

        达到阈值后重置计数器，实现周期性增量画像更新。
        """
        group_id = anything.group_id if isinstance(anything, EverythingGroup) else 0
        adapter_key = str(getattr(anything, "adapter_key", "") or "")
        user_entity = await self.everything_data.get_anything(group_id, anything.uid, adapter_key)
        conv_count = user_entity.add_conversations_num()
        has_personality = bool(user_entity.personality.get("personality"))

        if conv_count == 1 and not has_personality:
            uid_str = str(anything.uid)
            self.add_general_task(MindTask(
                task_type=TaskType.PROFILE,
                scope=user_entity.identity_scope,
                uid=anything.uid,
                preview=f"新用户 {uid_str} 首次出现，建立画像",
            ))
            self._enqueue_analysis(group_id, user_entity.uid or 0, adapter_key)
        elif conv_count >= self._analysis_threshold():
            self._enqueue_analysis(group_id, user_entity.uid or 0, adapter_key)
            user_entity.reset_conversations_num()

    # ==================================================================
    # 消息任务消费
    # ==================================================================

    def _clear_scope_state(self, scope: str) -> None:
        """清理 scope 消费后的关联状态（预览 / 路由 / 未读 / 群发送者）。"""
        self._message_previews.pop(scope, None)
        self._task_adapter_keys.pop(scope, None)
        self._unread_counts.pop(scope, None)
        self._group_recent_senders.pop(scope, None)

    async def pop_user_task(self) -> Optional[str]:
        """弹出下一个私聊待回复 scope（entity_scope 字符串）。"""
        if not self.pending_user.is_empty():
            scope = self.pending_user.popleft()
            self._clear_scope_state(scope)
            return scope
        return None

    async def pop_group_task(self) -> Optional[str]:
        """弹出下一个群聊待回复 scope（entity_scope 字符串）。"""
        if not self.pending_group.is_empty():
            scope = self.pending_group.popleft()
            self._clear_scope_state(scope)
            return scope
        return None

    async def pop_analysis_task(self) -> Optional[EntityData]:
        if not self.pending_analysis.is_empty():
            group_id, uid, adapter_key = self.pending_analysis.popleft()
            key = self._analysis_task_keys.pop((group_id, uid, adapter_key), "")
            self._persist_remove(key)
            return await self.everything_data.get_anything(group_id, uid, adapter_key)
        return None

    # ==================================================================
    # 通用任务队列
    # ==================================================================

    def add_general_task(self, task: MindTask) -> None:
        self._general_tasks.append(task)
        key = uuid.uuid4().hex
        self._general_task_keys.append(key)
        self._persist_add(scope=task.scope or "global", kind="general", payload={
            "task_key": key,
            "task_type": task.task_type.value,
            "scope": task.scope,
            "preview": task.preview,
            "adapter_key": task.adapter_key,
            "uid": task.uid,
            "group_id": task.group_id,
            "timestamp": task.timestamp,
            "metadata": task.metadata,
        })

    def peek_general_tasks(self) -> list[MindTask]:
        return list(self._general_tasks)

    def get_pending_message_previews(self) -> Dict[str, Tuple[str, str]]:
        """待处理消息预览快照：{scope: (preview, adapter_key)}。"""
        return {
            scope: (preview, self._task_adapter_keys.get(scope, ""))
            for scope, preview in self._message_previews.items()
        }

    def consume_general_task(self, index: int) -> bool:
        if 0 <= index < len(self._general_tasks):
            self._general_tasks.pop(index)
            key = self._general_task_keys.pop(index) if index < len(self._general_task_keys) else ""
            self._persist_remove(key)
            return True
        return False

    def clear_general_tasks(self) -> int:
        count = len(self._general_tasks)
        self._general_tasks.clear()
        keys, self._general_task_keys = self._general_task_keys, []
        for key in keys:
            self._persist_remove(key)
        return count

    def clear_general_tasks_before(self, snapshot_count: int) -> int:
        """快照式清理：只清快照前已存在的条目，周期内新增保留到下周期。"""
        if snapshot_count <= 0:
            return 0
        removed = min(snapshot_count, len(self._general_tasks))
        self._general_tasks = self._general_tasks[removed:]
        keys_to_remove = self._general_task_keys[:removed]
        self._general_task_keys = self._general_task_keys[removed:]
        for key in keys_to_remove:
            self._persist_remove(key)
        return removed

    # ==================================================================
    # 态势感知 / 路由
    # ==================================================================

    def peek_all_tasks(self) -> List[Tuple[str, str, str, str]]:
        """查看所有待处理消息任务（不消费）。

        返回 (scope, base_uid, base_group_id, preview) 四元组；
        base id 从 scope 解析（不含 adapter 前缀与 #session 后缀），私聊项 group_id 为 "0"，反之亦然。
        """
        from agent.messages import parse_entity_scope

        result: List[Tuple[str, str, str, str]] = []
        for scope in self.pending_user.queue:
            _, _, uid, _ = parse_entity_scope(scope)
            preview = self._message_previews.get(scope, "")
            result.append((scope, uid, "0", preview))
        for scope in self.pending_group.queue:
            _, _, gid, _ = parse_entity_scope(scope)
            preview = self._message_previews.get(scope, "")
            result.append((scope, "0", gid, preview))
        return result

    def consume_scope_task(self, scope: str) -> bool:
        """消费指定 scope 的待回复条目（含关联状态清理）。"""
        if not scope:
            return False
        self._clear_scope_state(scope)
        queue = self.pending_group if scope.startswith("group_") else self.pending_user
        return self._consume_from_queue(queue, scope)

    def get_unread_count(self, scope: str) -> int:
        """指定 scope 的未读消息数（消费后清零）。"""
        return self._unread_counts.get(scope, 0)

    @staticmethod
    def _consume_from_queue(queue: UniqueQueue, key: str) -> bool:
        """从去重队列中消费元素。"""
        if key in queue.seen:
            queue.seen.discard(key)
            try:
                queue.queue.remove(key)
            except ValueError:
                log("_consume_from_queue 异常已忽略", "DEBUG")
            return True
        return False

    def has_pending_tasks(self) -> bool:
        return (
                not self.pending_user.is_empty()
                or not self.pending_group.is_empty()
                or bool(self._general_tasks)
        )

    def set_adapter_key(self, scope: str, adapter_key: str) -> None:
        """注册 scope → adapter_key 映射（支撑主动消息路由）。"""
        if scope and adapter_key:
            self._task_adapter_keys[scope] = adapter_key

    def get_adapter_key(self, scope: str) -> str:
        return self._task_adapter_keys.get(scope, "")

    def set_message_preview(self, scope: str, preview: str) -> None:
        """登记 scope 的待处理消息预览（供态势收集与提示注入）。"""
        if scope:
            self._message_previews[scope] = preview

    # ==================================================================
    # 媒体收集
    # ==================================================================

    def collect_images(self, scope: str = "") -> List[ImageContent]:
        """收集并清空指定 scope 的待处理图片（无 scope 时取默认桶）。"""
        key = scope or "_default"
        images = self._pending_images.pop(key, [])
        return images

    def collect_media(self, scope: str = "") -> list:
        """收集并清空指定 scope 的待处理媒体片段（无 scope 时取默认桶）。"""
        key = scope or "_default"
        media = self._pending_media.pop(key, [])
        return media
