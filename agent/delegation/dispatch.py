"""模型委派入口的有界等待，任务执行和停止仍归 DelegationManager。

模型可见：短任务直接返回结果；等待到期或收到提醒时返回仍在执行的 ID 和进度。
Token 影响：等待不调用模型，完成结果只通过工具回执或后台通知领取一次。
缓存影响：仅工具回执变化，不向稳定前缀注入运行态。
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from agent.mind.tool_activation import current_owner_scope
from core.cancellation import get_execution_signal

if TYPE_CHECKING:
    from agent.delegation.delegation_manager import DelegationManager

# 小于默认工具执行期限；外层工具结束不拥有子代理的取消权。
FOREGROUND_WAIT_SECONDS = 30.0


async def dispatch_delegations(
    manager: DelegationManager, tasks: list[dict[str, Any]], *, background: bool = False,
) -> dict[str, Any]:
    """先托管全部执行，再有界等待；等待取消只释放结果认领，不取消子任务。"""
    manager.validate_batch_size(len(tasks))
    for task in tasks:
        if not isinstance(task.get("goal"), str) or not task["goal"].strip():
            raise ValueError("每个任务的 goal 必须是非空字符串")
        if not isinstance(task.get("context", ""), str) or not isinstance(task.get("role", "leaf"), str):
            raise ValueError("任务的 context 和 role 必须是字符串")
    scope = current_owner_scope()
    registry = getattr(manager._mind, "background_tasks", None)
    ids = [manager.delegate_background(**task, scope=scope, task_index=i) for i, task in enumerate(tasks)]
    handles = {did: manager._background_tasks[did] for did in ids}
    if background:
        return {"ok": True, "mode": "background", "delegation_id": ids[0] if len(ids) == 1 else "",
                "delegation_ids": ids, "completion_ready": False,
                "message": "子代理已受理；完成后通知，可用 check_background_tasks 查询、wait_background_tasks 等待或 terminate_background_task 停止。"}

    signal = get_execution_signal()
    initial_revision = manager._mind.pfc.message_revision if registry is not None else 0

    async def interrupted() -> bool:
        return bool(signal and signal.cancelled) or manager._mind.pfc.message_revision != initial_revision

    reason = "completed"
    with registry.reserve_results(scope, ids) if registry is not None else nullcontext():
        deadline = time.monotonic() + FOREGROUND_WAIT_SECONDS
        if registry is None:
            _, pending = await asyncio.wait(set(handles.values()), timeout=FOREGROUND_WAIT_SECONDS)
            reason = "timeout" if pending else "completed"
        else:
            for did in ids:
                waited = await registry.wait_any(scope, max(0, deadline - time.monotonic()), interrupted, task_id=did)
                if waited.reason != "completed":
                    reason = waited.reason
                    break
        done_ids = [did for did, task in handles.items() if task.done() and not task.cancelled()]
        results = [handles[did].result() for did in done_ids]
        payload: dict[str, Any] = json.loads(manager.aggregate_results(results))
        pending_ids = [did for did in ids if did not in done_ids]
        payload.update(
            mode="background" if pending_ids else "completed", reason=reason,
            delegation_ids=ids, pending_ids=pending_ids, total=len(ids),
            completion_ready=not pending_ids and payload["completion_ready"],
        )
        if len(ids) == 1:
            payload["delegation_id"] = ids[0]
        if pending_ids:
            payload["running"] = [item for item in manager.running_snapshot(scope)
                                  if item["delegation_id"] in pending_ids]
            payload["hint"] = "等待已结束，子代理仍在执行。请勿重复启动；用 check_background_tasks 查询、wait_background_tasks 等待，确认无需继续时 terminate_background_task。"
        if registry is not None:
            registry.claim_completed(scope, done_ids)
        return payload
