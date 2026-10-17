"""会话后台任务的查询、等待和终止接口，不依赖子代理是否启用。

模型可见：工具返回任务 ID、终态、耗时与结果，等待超时只表示仍未完成。
Token：等待期间不调用模型；完成结果按需读取。
缓存：只更新工具链与动态状态，不修改稳定上下文前缀。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from agent.mind.background.tasks import current_background_task_id
from agent.mind.tool_activation import current_owner_scope
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_registry import deferred_tool

from .ports import mind_port


@deferred_tool(group="thinking", tags=["always"], source="mind.background")
async def check_background_tasks(task_id: str = "", after_sequence: int = 0) -> str:
    """查询当前会话的后台任务，受理不代表已完成。

    Args:
        task_id: 留空列出任务；指定 ID 读取结果或增量输出。
        after_sequence: 操作证据游标，使用上次 evidence.next_sequence。
    """
    if not mind_port.bound:
        return tool_error("后台任务系统未就绪", cause=ErrorCause.STATE, retryable=True)
    registry = mind_port.get().background_tasks
    scope = current_owner_scope()
    result = (registry.read_task_output(scope, task_id, after_sequence=after_sequence)
              if task_id else registry.snapshot(scope))
    if result.get("ok") is False:
        return tool_error(result["error"], cause=ErrorCause.NOT_FOUND, retryable=False)
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(group="thinking", tags=["always"], source="mind.background", timeout=125)
async def wait_background_tasks(task_id: str, timeout: float = 30) -> str:
    """挂起等待指定后台任务，不消耗模型轮次；超时不取消任务。

    Args:
        task_id: 当前会话的后台任务 ID，不能等待自身。
        timeout: 本次最多等待秒数，范围 0–120；仍可用 end_reply 暂停本轮等待完成通知。
    """
    if not mind_port.bound:
        return tool_error("后台任务系统未就绪", cause=ErrorCause.STATE, retryable=True)
    if not task_id.strip():
        return tool_error("等待需要指定任务 ID", cause=ErrorCause.PARAM, retryable=False)
    if task_id == current_background_task_id.get():
        return tool_error("不能等待当前正在执行的任务自身", cause=ErrorCause.PARAM, retryable=False)
    mind = mind_port.get()
    scope = current_owner_scope()
    initial_revision = mind.pfc.message_revision

    async def interrupted() -> bool:
        return (mind.interrupts.is_requested(scope)
                or mind.pfc.message_revision != initial_revision)

    try:
        result = await mind.background_tasks.wait_any(
            scope, min(120, max(0, timeout)), interrupted, task_id=task_id,
        )
        return json.dumps({"ok": True, "reason": result.reason,
                           "completions": [asdict(item) for item in result.completions],
                           "hint": "只有 completed 表示完成；timeout/interrupted 不取消后台任务。"},
                          ensure_ascii=False)
    except Exception as exc:
        return error_from_exception(exc, action="等待后台任务")


@deferred_tool(group="thinking", tags=["always"], source="mind.background")
async def terminate_background_task(task_id: str) -> str:
    """请求终止当前会话的后台任务，最终状态由执行方确认。

    Args:
        task_id: check_background_tasks 返回的任务 ID。
    """
    if not mind_port.bound:
        return tool_error("后台任务系统未就绪", cause=ErrorCause.STATE, retryable=True)
    result = await asyncio.to_thread(mind_port.get().background_tasks.terminate, current_owner_scope(), task_id)
    if not result.get("ok"):
        return tool_error(result.get("error", "终止信号未受理"), cause=ErrorCause.STATE, retryable=False)
    return json.dumps(result, ensure_ascii=False)
