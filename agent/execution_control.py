"""实体、频道和 Web 共用的执行停止入口。

模型可见：停止只针对明确 scope/run_id，返回请求受理和后台工作取消事实。
Token：按需返回小型工具结果；无常驻注入。
缓存：不修改任何稳定前缀。
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from core.cancellation import get_execution_signal
from core.latebind import LateBinding
from core.tool_context import request_trace

if TYPE_CHECKING:
    from agent.delegation.delegation_manager import DelegationManager
    from agent.mind.mind import Mind

class StopHandler(Protocol):
    """运行时停止委托的目标与原因契约。"""

    async def __call__(self, *, scope: str, run_id: str, reason: str) -> dict[str, Any]: ...


execution_control_port: LateBinding[StopHandler] = LateBinding("execution_control")


async def stop_execution(
    mind: Mind, delegation_manager: DelegationManager | None, *,
    scope: str = "", run_id: str = "", reason: str = "请求停止执行",
) -> dict[str, Any]:
    """请求停止当前轮次及所属后台工作；受理不等同于资源已收束。"""
    if run_id:
        resolved = mind.interrupts.scope_for_run(run_id)
        if resolved is None or (scope and scope != resolved):
            return {"status": "idle"}
        scope = resolved
    if not scope:
        return {"status": "idle"}
    interrupted = mind.interrupt(scope, reason=reason)
    cancelled = delegation_manager.cancel_scope(scope) if delegation_manager is not None else 0
    background = await mind.background_tasks.cancel_scope(scope)
    if not interrupted and not cancelled and not background:
        return {"status": "idle"}
    return {"status": "ok", "interrupted": interrupted,
            "cancelled_delegations": cancelled, "background": background}


async def request_execution_stop(
    *, scope: str = "", run_id: str = "", reason: str = "请求停止执行",
) -> dict[str, Any]:
    """SDK 控制面：显式目标优先，省略时仅停止当前调用上下文。"""
    if not scope and not run_id:
        signal = get_execution_signal()
        if signal is not None:
            scope, run_id = signal.scope, signal.run_id
        else:
            scope = request_trace().get("scope", "")
    return await execution_control_port.get()(scope=scope, run_id=run_id, reason=reason)


def execution_stop_handler(mind: Mind, delegation_manager: DelegationManager | None) -> StopHandler:
    """组合根创建无状态控制委托，不拥有运行时资源。"""
    return partial(stop_execution, mind, delegation_manager)
