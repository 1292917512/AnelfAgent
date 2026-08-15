"""会话用量服务 -- per-scope 累计 LLM 用量与回复轮数查询（Web API 用）。

分层：web/routers → 本服务 → runtime（mind + data_center.sqlite）。
数据源为主库 scope_usage 表（增量累加账本，见 agent/mind/scope_usage.py）；
内存 snapshot 仅作尚未落盘窗口的补充视图。
"""

from __future__ import annotations

from typing import Any, Dict, List

from services._runtime import get_runtime


class UsageService:

    def list_usage(self, limit: int = 50) -> List[Dict[str, Any]]:
        """按总 token 降序列出会话累计用量（DB 权威值）。"""
        rt = get_runtime()
        if rt is None:
            return []
        try:
            return rt.mind.conversation_data.router.sqlite.list_scope_usage(limit)
        except Exception:
            return []

    def summary(self) -> Dict[str, Any]:
        """全量会话的合计视图（成本总览）。"""
        rows = self.list_usage(limit=500)
        totals = {
            "scopes": len(rows),
            "turns": sum(r.get("turns", 0) for r in rows),
            "llm_calls": sum(r.get("llm_calls", 0) for r in rows),
            "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
            "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
            "total_tokens": sum(r.get("total_tokens", 0) for r in rows),
            "cache_read_tokens": sum(r.get("cache_read_tokens", 0) for r in rows),
        }
        return totals

    def pending_snapshot(self) -> Dict[str, Dict[str, int]]:
        """内存中尚未落盘的增量视图（调试用；权威值在 DB）。"""
        from agent.mind.scope_usage import scope_usage_stats
        return scope_usage_stats.snapshot()
