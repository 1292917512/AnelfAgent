"""画像操作观测与恢复，以及普通记忆审计查询；AI 与 Web 共享同一事实源。

模型看到：分页操作、来源、理由、同步状态；指定操作时按需读取前后全文。
Token：摘要每页至多 50 项，全文只在明确查询时返回；无额外模型调用。
缓存：信息仅进入工具结果，不注入稳定前缀。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from core.tool_errors import error_from_exception
from core.tool_registry import deferred_tool

from .profiles import ProfileWriter


def _writer() -> ProfileWriter:
    from agent.runtime.singleton import require_runtime
    runtime = require_runtime()
    return ProfileWriter(runtime.data_center.everything_data, runtime.memory_store)


@deferred_tool(group="memory", tags=["core", "heartbeat"], source="mind.memory",
               description="查询画像操作时间线和具体修订：来源反思、理由、同步状态、前后全文。AI 可据此自主修订，无需人工审核。历史保留通过 memory/profile_history 配置组管理。")
async def profile_history(
    scope_type: str = "", scope_id: str = "", operation_id: str = "", before_id: int = 0, limit: int = 20,
) -> str:
    """读取已有画像操作，不把查询计作证据或新的修订。

    Args:
        scope_type: user/group/agent；留空查询全局
        scope_id: 身份 ID；自画像为 self，其他身份使用含频道的完整 ID
        operation_id: 指定时返回该操作前后全文，忽略分页参数
        before_id: 上一页 next_cursor，0 表示最新
        limit: 每页操作数（1 到 50）
    """
    try:
        writer = _writer()
        if operation_id:
            operation = await writer.operations.get_operation(operation_id)
            return json.dumps(asdict(operation) if operation else {"found": False}, ensure_ascii=False)
        if scope_type:
            current = await writer.read(scope_type, scope_id)
            scope_type, scope_id = current["scope_type"], current["scope_id"]
        limit = max(1, min(50, limit))
        items = await writer.operations.timeline(scope_type=scope_type, scope_id=scope_id,
                                                  before_id=max(0, before_id), limit=limit + 1)
        return json.dumps({"items": [item.summary() for item in items[:limit]],
                           "next_cursor": items[limit - 1].id if len(items) > limit else None}, ensure_ascii=False)
    except Exception as exc:
        return error_from_exception(exc, action="查询画像操作")


@deferred_tool(group="memory", tags=["core", "heartbeat"], source="mind.memory",
               description="重试已提交画像的同步；不重跑模型、不重写后来修订。留空按维护批次恢复并执行配置的历史保留策略。")
async def sync_profiles(operation_id: str = "") -> str:
    """恢复画像镜像和在线缓存。

    Args:
        operation_id: 要恢复的操作 ID；留空恢复待同步批次
    """
    try:
        writer = _writer()
        if operation_id:
            result = await writer.resume(operation_id)
            return json.dumps(asdict(result) if result else {"found": False}, ensure_ascii=False)
        return json.dumps({"synced": await writer.recover()}, ensure_ascii=False)
    except Exception as exc:
        return error_from_exception(exc, action="恢复画像同步")


@deferred_tool(group="memory", tags=["core", "heartbeat"], source="mind.memory",
               description="分页查询记忆操作时间线：创建、修订、合并、归档、反馈与晋升状态。记录用于解释操作，不作为新增认知证据。")
async def memory_history(memory_id: int = 0, before_id: int = 0, limit: int = 20) -> str:
    """按需读取审计窗口内的记忆操作。

    Args:
        memory_id: 记忆 ID，0 查询全局
        before_id: 上一页 next_cursor，0 表示最新
        limit: 每页数量（1 到 50）
    """
    try:
        from agent.runtime.singleton import require_runtime
        from core.config import get_config_int
        store = require_runtime().memory_store
        if store is None:
            raise RuntimeError("记忆库未就绪")
        limit = max(1, min(50, limit))
        items = await store.list_audit(max(0, memory_id), limit + 1, before_id=max(0, before_id))
        return json.dumps({"items": items[:limit],
                           "next_cursor": items[limit - 1]["id"] if len(items) > limit else None,
                           "retention_days": get_config_int("memory_audit_retention_days", 30)}, ensure_ascii=False)
    except Exception as exc:
        return error_from_exception(exc, action="查询记忆操作")
