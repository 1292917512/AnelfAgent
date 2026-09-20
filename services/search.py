"""全局搜索服务 — 聚合记忆、日志、工作区文件、会话记录的统一搜索。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from core.log import log, query_log_buffer
from services._runtime import get_runtime
from services.workspace import WorkspaceService


class SearchService:
    """全局搜索聚合（各数据源互相独立，单源失败降级为空不影响其他源）。"""

    async def global_search(self, q: str, limit: int) -> Dict[str, Any]:
        """聚合记忆 / 日志 / 工作区文件 / 会话记录四路搜索。"""
        workspace_svc = WorkspaceService()
        memory, conversations, files = await asyncio.gather(
            self._search_memory(q, limit),
            self._search_conversations(q, limit),
            asyncio.to_thread(workspace_svc.search_files, q, limit),
        )
        logs = query_log_buffer(keyword=q, limit=limit)
        return {
            "query": q,
            "memory": memory,
            "logs": logs,
            "files": files["files"],
            "conversations": conversations,
        }

    @staticmethod
    async def _search_memory(q: str, limit: int) -> List[Dict[str, Any]]:
        """搜索长期记忆（复用记忆服务的混合检索）。"""
        try:
            from services import MemoryService
            results = await MemoryService().search_ltm(query=q, limit=limit)
            return [
                {
                    "id": r.get("id"), "snippet": r.get("snippet", ""),
                    "memory_type": r.get("memory_type", ""), "tags": r.get("tags", []),
                    "score": r.get("score", 0),
                }
                for r in results
            ]
        except Exception as e:
            log(f"全局搜索记忆失败: {e}", "DEBUG")
            return []

    @staticmethod
    async def _search_conversations(q: str, limit: int) -> List[Dict[str, Any]]:
        """跨 scope 搜索会话消息。"""
        try:
            rt = get_runtime()
            if rt is None:
                return []
            rows = await rt.data_center.sqlite.search_conversation_global(q, limit=limit)
            results: List[Dict[str, Any]] = []
            for r in rows:
                ts_ns = r.get("ts_ns") or 0
                ts = ts_ns / 1e9 if ts_ns > 1e15 else ts_ns
                results.append({
                    "id": r.get("id"),
                    "scope": f"{r.get('scope_type')}:{r.get('scope_id')}",
                    "role": r.get("role", ""),
                    "snippet": str(r.get("content", ""))[:200],
                    "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
                })
            return results
        except Exception as e:
            log(f"全局搜索会话失败: {e}", "DEBUG")
            return []
