"""记忆管理服务 -- STM/LTM/Conv/Entity/Notes/FileIndex 六个子域的增删改查。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log
from services._runtime import require_runtime


def _parse_memory_type(type_str: Optional[str]):
    """解析 MemoryType 字符串，无效时返回 None。"""
    if not type_str:
        return None
    from agent.core.mind.memory.memory_types import MemoryType
    try:
        return MemoryType(type_str)
    except ValueError:
        return None


class MemoryService:

    # ==================================================================
    # 短期记忆（PFC temporary）
    # ==================================================================

    def list_stm(self) -> List[Dict[str, Any]]:
        """返回 PFC 短期记忆列表。"""
        rt = require_runtime()
        return list(rt.mind.pfc.temporary)

    def delete_stm(self, index: int) -> bool:
        rt = require_runtime()
        return rt.mind.pfc.delete_temporary(index)

    def clear_stm(self) -> int:
        rt = require_runtime()
        return rt.mind.pfc.clear_temporary()

    def get_pfc_status(self) -> List[Dict[str, Any]]:
        """获取 PFC 的队列状态信息（待处理消息、通用任务等）。"""
        rt = require_runtime()
        pfc = rt.mind.pfc
        result: List[Dict[str, Any]] = []

        for scope, preview in pfc._message_previews.items():
            adapter_key = pfc._task_adapter_keys.get(scope, "")
            result.append({
                "role": "pending",
                "content": f"[待处理消息: {scope}] {preview}" + (f" [{adapter_key}]" if adapter_key else ""),
            })

        for task in pfc.peek_general_tasks():
            result.append({
                "role": "task",
                "content": f"[任务: {task.task_type.value}] {task.scope}: {task.preview}",
            })

        if pfc.pending_analysis and not pfc.pending_analysis.is_empty():
            result.append({
                "role": "analysis",
                "content": f"[待分析实体] {len(pfc.pending_analysis)} 个",
            })

        return result

    # ==================================================================
    # 长期记忆（MemoryStore）
    # ==================================================================

    async def list_ltm(
        self, memory_type: Optional[str] = None, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return []
        return await store.list_all_with_id(
            memory_type=_parse_memory_type(memory_type), limit=limit,
        )

    async def get_ltm(self, mem_id: int) -> Any:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return None
        return await store.get(mem_id)

    async def delete_ltm(self, mem_id: int) -> bool:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return False
        return await store.delete(mem_id)

    async def update_ltm(self, mem_id: int, content: str, importance: float = 0.5, tags: Optional[List[str]] = None) -> bool:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return False
        entry = await store.get(mem_id)
        if not entry:
            return False
        entry.content = content
        entry.importance = importance
        if tags is not None:
            entry.tags = tags
        return await store.update(entry)

    async def create_ltm(self, content: str, memory_type: str = "semantic", importance: float = 0.5, tags: Optional[List[str]] = None) -> int:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return -1
        from agent.core.mind.memory.memory_types import MemoryEntry, MemoryType
        mt = _parse_memory_type(memory_type) or MemoryType.SEMANTIC
        entry = MemoryEntry(content=content, memory_type=mt, importance=importance, source="webui", tags=tags or [])
        return await store.add(entry)

    async def clear_ltm(self, memory_type: Optional[str] = None) -> int:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return 0
        return await store.clear(_parse_memory_type(memory_type))

    async def list_ltm_paginated(
        self, page: int = 1, page_size: int = 50, memory_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"items": [], "total": 0, "page": 1, "page_size": page_size, "pages": 0}
        return await store.list_paginated(
            page=page, page_size=page_size,
            memory_type=_parse_memory_type(memory_type),
        )

    async def get_ltm_stats(self) -> Dict[str, Any]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"type_counts": {}, "total": 0}
        type_counts = await store.get_type_counts()
        total = sum(type_counts.values())
        return {"type_counts": type_counts, "total": total}

    async def search_ltm(self, query: str, tags: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return []
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        query_vec = None
        try:
            if rt.mind.embedder and rt.mind.embedder.available:
                query_vec = await rt.mind.embedder.embed_one(query)
        except Exception as e:
            log(f"搜索记忆时 embedding 失败: {e}", "DEBUG")
        results = await store.search_unified(
            query=query, query_vec=query_vec, query_tags=tag_list, limit=limit,
        )
        return [
            {
                "id": r.id, "snippet": r.snippet[:300], "score": round(r.score, 3),
                "source": r.source, "memory_type": r.memory_type or "",
                "tags": r.tags, "path": r.path,
            }
            for r in results
        ]

    async def merge_ltm(self, ids: List[int], content: str) -> Dict[str, Any]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        new_id = await store.merge_memories(ids, content)
        if not new_id:
            return {"error": "合并失败"}
        return {"ok": True, "new_id": new_id, "merged_from": ids}

    # ==================================================================
    # 会话记录
    # ==================================================================

    async def list_conv_scopes(self) -> List[Dict[str, Any]]:
        rt = require_runtime()
        return await rt.data_center.sqlite.list_conversation_scopes()

    async def list_conv_messages(
        self, scope_type: str, scope_id: str, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        rt = require_runtime()
        return await rt.data_center.sqlite.fetch_conversation_with_id(
            scope_type=scope_type, scope_id=scope_id, limit=limit,
        )

    async def delete_conv(self, row_id: int) -> None:
        rt = require_runtime()
        await rt.data_center.sqlite.delete_conversation_by_id(row_id)

    async def clear_conv(self, scope_type: str, scope_id: str) -> int:
        rt = require_runtime()
        return await rt.data_center.sqlite.clear_conversation(
            scope_type=scope_type, scope_id=scope_id,
        )

    # ==================================================================
    # 实体画像
    # ==================================================================

    async def list_entities(self) -> List[Dict[str, Any]]:
        rt = require_runtime()
        return await rt.data_center.sqlite.list_entity_profiles()

    async def save_entity(
        self, scope_type: str, scope_id: str, personality: str,
    ) -> None:
        rt = require_runtime()
        old = await rt.data_center.sqlite.get_entity_personality(
            scope_type=scope_type, scope_id=scope_id,
        )
        conv_num = old.get("conv_num", 0) if old else 0
        conv_update_num = old.get("conv_update_num", 0) if old else 0
        await rt.data_center.sqlite.set_entity_personality(
            scope_type=scope_type, scope_id=scope_id, personality=personality,
            conv_num=conv_num, conv_update_num=conv_update_num,
        )

    async def delete_entity(self, scope_type: str, scope_id: str) -> None:
        rt = require_runtime()
        await rt.data_center.sqlite.delete_entity_profile(
            scope_type=scope_type, scope_id=scope_id,
        )

    # ==================================================================
    # 实体别名（跨平台身份关联）
    # ==================================================================

    async def list_entity_aliases(self) -> List[Dict[str, Any]]:
        rt = require_runtime()
        return await rt.data_center.sqlite.list_aliases()

    async def link_entity(
        self,
        source_scope_type: str,
        source_scope_id: str,
        target_scope_type: str,
        target_scope_id: str,
    ) -> None:
        rt = require_runtime()
        sqlite = rt.data_center.sqlite
        target_primary = await sqlite.resolve_alias(target_scope_type, target_scope_id)
        p_type, p_id = target_primary if target_primary else (target_scope_type, target_scope_id)
        await sqlite.set_alias(
            scope_type=source_scope_type, scope_id=source_scope_id,
            primary_scope_type=p_type, primary_scope_id=p_id,
        )

    async def unlink_entity(self, scope_type: str, scope_id: str) -> bool:
        rt = require_runtime()
        return await rt.data_center.sqlite.remove_alias(
            scope_type=scope_type, scope_id=scope_id,
        )

    # ==================================================================
    # 便签记忆（MEMORY.md + memory/）
    # ==================================================================

    @staticmethod
    def read_notes() -> str:
        from agent.core.mind.memory.notes import load_notes_content
        return load_notes_content()

    @staticmethod
    def write_notes(content: str) -> None:
        from agent.core.mind.memory.notes import get_notes_path, _atomic_write
        p = get_notes_path()
        _atomic_write(p, content)

    @staticmethod
    def get_notes_path() -> str:
        from agent.core.mind.memory.notes import get_notes_path
        return str(get_notes_path())

    @staticmethod
    def list_memory_files() -> List[Dict[str, str]]:
        from agent.core.mind.memory.notes import list_all_memory_files
        return list_all_memory_files()

    @staticmethod
    def read_memory_file(file_path: str) -> str:
        from agent.core.mind.memory.notes import read_memory_file
        return read_memory_file(file_path)

    @staticmethod
    def write_memory_file(file_path: str, content: str) -> int:
        from agent.core.mind.memory.notes import write_memory_file
        return write_memory_file(file_path, content)

    # ==================================================================
    # 文件索引状态
    # ==================================================================

    async def get_index_status(self) -> Dict[str, Any]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        return await store.get_index_status()

    async def resync_files(self, force: bool = False) -> Dict[str, int]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        from agent.core.mind.memory.memory_sync import sync_files
        from agent.core.mind.memory.notes import get_workspace_dir
        return await sync_files(store, rt.mind.embedder, get_workspace_dir(), force=force)

    async def clean_embedding_cache(self) -> Dict[str, int]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        cleaned = await store.clean_embedding_cache()
        return {"cleaned": cleaned}

    async def get_health_status(self) -> Dict[str, Any]:
        """返回记忆系统综合健康状态。"""
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        health = await store.get_health_status()
        embedder = rt.mind.embedder
        health["embedding_available"] = embedder.available if embedder else False
        health["embedding_dims"] = embedder.dimensions if embedder else None
        return health

    # ==================================================================
    # 目标计划（Goals）
    # ==================================================================

    _GOAL_SOURCE = "goal"

    async def list_goals(self, status: str = "all") -> List[Dict[str, Any]]:
        """列出所有目标计划。"""
        import json
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return []
        from agent.core.mind.memory.memory_types import MemoryType
        entries = await store.list_recent(limit=100, memory_type=MemoryType.SEMANTIC, source=self._GOAL_SOURCE)
        goals: List[Dict[str, Any]] = []
        for entry in entries:
            try:
                goal = json.loads(entry.content)
                goal["memory_id"] = entry.id
                goal["created_ts"] = entry.created_at
                if status == "all" or goal.get("status") == status:
                    goals.append(goal)
            except (json.JSONDecodeError, AttributeError):
                continue
        return goals

    async def get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """获取单个目标详情。"""
        import json
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return None
        from agent.core.mind.memory.memory_types import MemoryType
        entries = await store.list_recent(limit=100, memory_type=MemoryType.SEMANTIC, source=self._GOAL_SOURCE)
        for entry in entries:
            try:
                goal = json.loads(entry.content)
                if goal.get("goal_id") == goal_id:
                    goal["memory_id"] = entry.id
                    return goal
            except (json.JSONDecodeError, AttributeError):
                continue
        return None

    async def create_goal(
        self, title: str, description: str = "", steps: Optional[List[str]] = None,
        due_time: Optional[str] = None, recurring: bool = False,
    ) -> Dict[str, Any]:
        """创建新目标。"""
        import json
        import time
        import uuid
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        from agent.core.mind.memory.memory_types import MemoryEntry, MemoryType
        goal: Dict[str, Any] = {
            "goal_id": uuid.uuid4().hex[:8],
            "title": title,
            "description": description,
            "status": "active",
            "recurring": recurring,
            "steps": [
                {"index": i, "content": s, "status": "pending", "note": ""}
                for i, s in enumerate(steps or [])
            ],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if due_time:
            goal["due_time"] = due_time
        entry = MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(goal, ensure_ascii=False),
            source=self._GOAL_SOURCE,
            importance=0.8,
            metadata={"goal_id": goal["goal_id"], "status": "active"},
        )
        entry_id = await store.add(entry)
        goal["memory_id"] = entry_id
        return goal

    async def update_goal(
        self,
        goal_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        due_time: Optional[str] = None,
        recurring: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """更新目标。循环计划完成时自动重置步骤。"""
        import json
        import time
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return None
        from agent.core.mind.memory.memory_types import MemoryEntry, MemoryType
        entries = await store.list_recent(limit=100, memory_type=MemoryType.SEMANTIC, source=self._GOAL_SOURCE)
        target_entry = None
        target_goal = None
        for entry in entries:
            try:
                goal = json.loads(entry.content)
                if goal.get("goal_id") == goal_id:
                    target_entry = entry
                    target_goal = goal
                    break
            except (json.JSONDecodeError, AttributeError):
                continue
        if target_entry is None or target_goal is None:
            return None
        if title is not None:
            target_goal["title"] = title
        if description is not None:
            target_goal["description"] = description
        if recurring is not None:
            target_goal["recurring"] = recurring
        if due_time is not None:
            target_goal["due_time"] = due_time if due_time else None
        if status is not None:
            if status == "completed" and target_goal.get("recurring"):
                for s in target_goal.get("steps", []):
                    s["status"] = "pending"
                    s["note"] = ""
                target_goal["status"] = "active"
            else:
                target_goal["status"] = status
        if steps is not None:
            target_goal["steps"] = steps
        target_goal["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if target_entry.id:
            await store.delete(target_entry.id)
        new_entry = MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(target_goal, ensure_ascii=False),
            source=self._GOAL_SOURCE,
            importance=0.8 if target_goal["status"] == "active" else 0.3,
            metadata={"goal_id": goal_id, "status": target_goal["status"]},
        )
        new_id = await store.add(new_entry)
        target_goal["memory_id"] = new_id
        return target_goal

    async def delete_goal(self, goal_id: str) -> bool:
        """删除目标。"""
        import json
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return False
        from agent.core.mind.memory.memory_types import MemoryType
        entries = await store.list_recent(limit=100, memory_type=MemoryType.SEMANTIC, source=self._GOAL_SOURCE)
        for entry in entries:
            try:
                goal = json.loads(entry.content)
                if goal.get("goal_id") == goal_id and entry.id:
                    await store.delete(entry.id)
                    return True
            except (json.JSONDecodeError, AttributeError):
                continue
        return False
