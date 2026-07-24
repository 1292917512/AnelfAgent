"""记忆管理服务 -- STM/LTM/Conv/Entity/Notes/FileIndex 六个子域的增删改查。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from services._runtime import require_runtime

_DOC_PREFIX = "uploads/docs/"
_DOC_MAX_SIZE = 20 * 1024 * 1024


def _docs_dir() -> Path:
    from core.path import ConfigPaths, project_root
    return Path(project_root()) / ConfigPaths.UPLOAD_DIR / "docs"


def _parse_memory_type(type_str: Optional[str]):
    """解析 MemoryType 字符串，无效时返回 None。"""
    if not type_str:
        return None
    from agent.memory.memory_types import MemoryType
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
        from agent.memory.memory_types import MemoryEntry, MemoryType
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
        from agent.memory.cognee.config import load_cognee_config
        from agent.memory.cognee.fusion import federated_search
        from agent.memory.cognee.runtime import get_cognee_client
        cognee_config = load_cognee_config()
        results = await federated_search(
            store.search_unified(
                query=query,
                query_vec=query_vec,
                query_tags=tag_list,
                limit=limit * cognee_config.recall_pool_multiplier,
            ),
            query=query,
            client=get_cognee_client(),
            config=cognee_config,
            limit=limit,
            query_tags=tag_list,
        )
        return [
            {
                "id": r.id, "snippet": r.snippet[:300], "score": round(r.score, 3),
                "source": r.source, "memory_type": r.memory_type or "",
                "tags": r.tags, "path": r.path, "dataset": r.dataset_name,
                "provenance": r.provenance,
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
    # 便签记忆（memory.md + memory/）
    # ==================================================================

    @staticmethod
    def read_notes() -> str:
        from agent.memory.notes import load_notes_content
        return load_notes_content()

    @staticmethod
    def write_notes(content: str) -> None:
        from agent.memory.notes import get_notes_path, _atomic_write
        p = get_notes_path()
        _atomic_write(p, content)

    @staticmethod
    def get_notes_path() -> str:
        from agent.memory.notes import get_notes_path
        return str(get_notes_path())

    @staticmethod
    def list_memory_files() -> List[Dict[str, str]]:
        from agent.memory.notes import list_all_memory_files
        return list_all_memory_files()

    @staticmethod
    def read_memory_file(file_path: str) -> str:
        from agent.memory.notes import read_memory_file
        return read_memory_file(file_path)

    @staticmethod
    def write_memory_file(file_path: str, content: str) -> int:
        from agent.memory.notes import write_memory_file
        return write_memory_file(file_path, content)

    @staticmethod
    def delete_memory_file(file_path: str) -> bool:
        """删除指定 MD 便签文件。主便签 memory.md 不允许删除。"""
        from agent.memory.notes import delete_memory_file, get_notes_path, get_workspace_dir
        main_rel = str(get_notes_path().relative_to(get_workspace_dir())).replace("\\", "/")
        if file_path == main_rel:
            raise ValueError("主便签不允许删除")
        return delete_memory_file(file_path)

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
        from agent.memory.memory_sync import sync_files
        from agent.memory.notes import get_workspace_dir
        return await sync_files(store, rt.mind.embedder, get_workspace_dir(), force=force)

    async def clean_embedding_cache(self) -> Dict[str, int]:
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        cleaned = await store.clean_embedding_cache()
        return {"cleaned": cleaned}

    # ==================================================================
    # 文档索引（uploads/docs 下的 PDF/Word/文本）
    # ==================================================================

    async def upload_document(self, filename: str, content: bytes) -> Dict[str, Any]:
        """保存上传文档并立即解析入库，返回索引统计。"""
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        from agent.memory.doc_extract import SUPPORTED_DOC_EXTS

        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_DOC_EXTS:
            return {"error": f"不支持的文档类型: {ext or '无扩展名'}"}
        if not content:
            return {"error": "文件内容为空"}
        if len(content) > _DOC_MAX_SIZE:
            return {"error": "文件超过 20MB 上限"}

        docs_dir = _docs_dir()
        docs_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{int(time.time() * 1000)}_{Path(filename).name}"
        dest = docs_dir / safe_name
        dest.write_bytes(content)

        rel_key = f"{_DOC_PREFIX}{safe_name}"
        from agent.memory.memory_sync import index_single_file
        chunks = await index_single_file(store, rt.mind.embedder, dest, rel_key, force=True)
        if chunks <= 0:
            dest.unlink(missing_ok=True)
            return {"error": "文档解析失败或无可用文本"}
        return {
            "ok": True, "path": rel_key, "name": Path(filename).name,
            "size": len(content), "chunks": chunks,
        }

    async def list_documents(self) -> List[Dict[str, Any]]:
        """列出已索引的上传文档。"""
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return []
        counts = await store.list_chunk_counts()
        docs: List[Dict[str, Any]] = []
        for f in await store.list_files():
            path = f["path"]
            if not path.startswith(_DOC_PREFIX):
                continue
            name = path[len(_DOC_PREFIX):]
            ts_prefix, _, display = name.partition("_")
            docs.append({
                "path": path,
                "name": display if ts_prefix.isdigit() and display else name,
                "size": f["size"],
                "chunks": counts.get(path, 0),
                "indexed_at": f["mtime_ns"] / 1e9,
            })
        return docs

    async def delete_document(self, path: str) -> Dict[str, Any]:
        """删除上传文档：清理索引并移除磁盘文件。"""
        rt = require_runtime()
        store = rt.mind.memory_store
        if not store:
            return {"error": "记忆系统未初始化"}
        if not path.startswith(_DOC_PREFIX) or ".." in path:
            return {"error": "非法文档路径"}
        await store.delete_file(path)
        from core.path import ConfigPaths, project_root
        disk = Path(project_root()) / ConfigPaths.UPLOAD_DIR / path[len("uploads/"):]
        try:
            disk.unlink(missing_ok=True)
        except Exception as exc:
            log(f"文档磁盘删除失败 [{disk}]: {exc}", "WARNING")
        return {"ok": True}

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
        health["cognee"] = await self.get_cognee_status()
        return health

    # ==================================================================
    # Cognee 可选后端
    # ==================================================================

    @staticmethod
    async def get_cognee_status() -> Dict[str, Any]:
        from agent.memory.cognee.config import load_cognee_config
        from agent.memory.cognee.runtime import (
            get_cognee_client,
            get_cognee_coordinator,
        )

        config = load_cognee_config()
        client = get_cognee_client()
        coordinator = get_cognee_coordinator()
        availability = (
            client.availability()
            if client
            else {
                "installed": False,
                "enabled": config.enabled,
                "ready": False,
                "version": "",
                "reason": "运行时未初始化",
            }
        )
        availability_data = (
            availability.model_dump()
            if hasattr(availability, "model_dump")
            else availability
        )
        sync = await coordinator.status() if coordinator else None
        return {
            "availability": availability_data,
            "resolved": client.resolved_info if client else {},
            "sync": sync.model_dump() if sync else {
                "enabled": config.enabled and config.sync_enabled,
                "running": False,
                "pending": 0,
                "failed": 0,
                "synced": 0,
                "last_error": "",
            },
        }

    @staticmethod
    def get_cognee_config() -> Dict[str, Any]:
        from agent.memory.cognee.config import load_cognee_config
        return load_cognee_config().to_dict()

    @staticmethod
    async def save_cognee_config(values: Dict[str, Any]) -> Dict[str, Any]:
        from agent.memory.cognee.config import (
            CogneeConfig,
            load_cognee_config,
            save_cognee_config,
        )
        from agent.memory.cognee.runtime import get_cognee_coordinator

        current = load_cognee_config().to_dict()
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(value)
            else:
                current[key] = value
        allowed = CogneeConfig.__dataclass_fields__.keys()
        config = CogneeConfig(**{
            key: value for key, value in current.items() if key in allowed
        }).normalized()
        save_cognee_config(config)

        # 热更新运行时，保存即生效（worker 启停 + 模型重映射）
        coordinator = get_cognee_coordinator()
        if coordinator:
            await coordinator.reconfigure(config)
        return config.to_dict()

    @staticmethod
    async def retry_cognee_sync() -> int:
        from agent.memory.cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        return await coordinator.retry_failed() if coordinator else 0

    @staticmethod
    async def backfill_cognee(
        *,
        limit: int = 0,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        from agent.memory.cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        if not coordinator:
            return {"error": "Cognee 运行时未初始化"}
        return await coordinator.backfill(limit=limit, dry_run=dry_run)

    @staticmethod
    async def list_cognee_datasets() -> List[Dict[str, Any]]:
        from agent.memory.cognee.runtime import get_cognee_client
        client = get_cognee_client()
        if not client:
            return []
        datasets = await client.list_datasets()
        return [
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item) if isinstance(item, dict)
            else {"id": str(getattr(item, "id", "")), "name": str(getattr(item, "name", ""))}
            for item in datasets
        ]

    @staticmethod
    async def improve_cognee(dataset_name: str) -> Any:
        from agent.memory.cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        if not coordinator:
            return {"error": "Cognee 运行时未初始化"}
        result = await coordinator.improve(dataset_name)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result

    @staticmethod
    async def get_cognee_graph_html(dataset: Optional[str] = None) -> str:
        """渲染 Cognee 官方知识图谱为自包含交互式 HTML。

        Args:
            dataset: 数据集名称；空值自动回退到首个可用数据集
                （访问控制开启时 cognee 强制要求指定数据集）。

        Raises:
            RuntimeError: Cognee 未就绪、无数据集或渲染失败。
        """
        from pathlib import Path

        from core.path import ConfigPaths, PathManager
        from agent.memory.cognee.graph_html import sanitize_cognee_graph_html
        from agent.memory.cognee.runtime import get_cognee_client

        client = get_cognee_client()
        if not client:
            raise RuntimeError("Cognee 运行时未初始化")
        if not client.availability().ready:
            raise RuntimeError("Cognee 未就绪")

        target = dataset
        if not target:
            names = [
                str(item.get("name", ""))
                for item in await MemoryService.list_cognee_datasets()
            ]
            names = [name for name in names if name]
            if not names:
                raise RuntimeError("Cognee 暂无数据集可渲染")
            target = "main_dataset" if "main_dataset" in names else names[0]

        out_dir = Path(ConfigPaths.COGNEE_DATA_DIR)
        PathManager.ensure_dir_exists(str(out_dir))
        # 可视化不应占用流水线级超时（可至 1800s）；过长会导致前端一直「渲染中」
        graph_timeout = min(max(float(client.config.timeout_seconds), 90.0), 180.0)
        try:
            html = await client.visualize_graph(
                destination_file_path=str(out_dir / "graph.html"),
                dataset=target,
                include_session_events=False,
                timeout=graph_timeout,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Cognee 图谱渲染失败: {exc}") from exc
        return sanitize_cognee_graph_html(str(html))

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
        from agent.memory.memory_types import MemoryType
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
        from agent.memory.memory_types import MemoryType
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
        from agent.memory.memory_types import MemoryEntry, MemoryType
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
        from agent.memory.memory_types import MemoryEntry, MemoryType
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
        from agent.memory.memory_types import MemoryType
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
