"""思维工具执行前的 hook、权限裁决与当前任务反馈。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Optional

from core.event_bus import EVENT_THINKING_TOOL_END, event_bus
from core.log import log

if TYPE_CHECKING:
    from agent.llm.types import ToolCall
    from agent.messages import Everything
    from agent.mind.mind import Mind


async def check_tool_permission(
        tc: "ToolCall", anything: Optional["Everything"], tool_scope: str,
        mind: Optional["Mind"] = None,
) -> Optional[str]:
    """检查工具权限，将拒绝原因返回模型，将风险提醒写入当前任务尾部。

    Model Experience:
    - 拒绝包含具体原因和不可重试标记；降级提醒仅面向 AI，不发送频道消息。
    - 常规放行无额外 token；提醒按会话节流，不改稳定上下文前缀。
    """
    from agent.approval import get_approval_gate
    from agent.mind.tool_activation import ToolActivationManager

    scope = tool_scope or ToolActivationManager.current_scope()
    interrupts = getattr(mind, "interrupts", None) if mind is not None else None

    def interrupted() -> bool:
        return bool(interrupts is not None and scope and interrupts.is_requested(scope))

    async def blocked(error: str, outcome: str) -> str:
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "scope": tool_scope, "tool_name": tc.name, "tool_id": tc.id,
            "duration_ms": 0, "error": error, "success": False,
        })
        return json.dumps({
            "error": error, "permission_outcome": outcome, "retryable": False,
            "hint": "请根据原因调整操作，不要重复相同调用或通过其他执行通道绕过规则。",
        }, ensure_ascii=False)

    outcome: str
    try:
        if interrupted():
            return await blocked("工具调用已取消，等待新的任务指示", "cancelled")
        # 用户 hook（tool_pre）：只能在审批规则之上收紧（exit 2 阻塞），
        # 不能放宽 DENY/ASK——hook 放行不构成授权。空配置时零开销短路
        from agent.hooks import hooks_active, run_event_hooks
        if hooks_active("tool_pre"):
            hook_outcome = await run_event_hooks(
                "tool_pre", tool_name=tc.name, arguments=tc.arguments or "",
                scope=tool_scope,
            )
            if not hook_outcome.allowed:
                return await blocked(f"工具调用被 hook 阻塞: {hook_outcome.reason}", "hook_blocked")


        try:
            tool_args = json.loads(tc.arguments) if tc.arguments else {}
        except (ValueError, TypeError):
            return await blocked("工具参数必须是有效 JSON 对象", "invalid_arguments")
        if not isinstance(tool_args, dict):
            return await blocked("工具参数必须是 JSON 对象", "invalid_arguments")
        result = await get_approval_gate().check(
            tool_name=tc.name, tool_args=tool_args, reason=f"AI 调用工具 {tc.name}", scope=scope,
            channel_id=str(getattr(anything, "adapter_key", "") or ""),
            user_id=str(getattr(anything, "uid", "") or ""),
        )
        if interrupted():
            return await blocked("工具调用已取消，等待新的任务指示", "cancelled")
        if result.notice:
            _push_approval_reminder(mind, scope, result.notice)
        if result.allowed:
            return None
        error, outcome = result.reason, result.outcome
    except asyncio.CancelledError:
        await blocked("权限评审已取消，工具未执行", "cancelled")
        raise
    except Exception as exc:
        log(f"工具权限检查失败: {type(exc).__name__}", "ERROR", tag="权限")
        error, outcome = "权限检查未完成，本次工具未执行", "permission_error"
    return await blocked(error, outcome)


# 提醒节流：(scope, 提醒文本) → 上次提醒时间——同一事件短时间去重，
# 不同事件（不同工具/不同原因）互不压制
_APPROVAL_REMINDER_AT: dict[tuple[str, str], float] = {}
_APPROVAL_REMINDER_MIN_INTERVAL = 120.0


def _push_approval_reminder(
        mind: Optional["Mind"], scope: str, text: str) -> None:
    """经 PushHub 给 AI 本身写一条审批提醒（写对话历史 + think_loop 轮内并入）。

    提醒的读者是模型自己：guardian 的疑虑/审批链路异常进上下文，由模型
    复核后自行决定是否告知用户。节流去重同一事件；push 失败静默
    （提醒是 best-effort，不影响放行路径）。
    """
    push_hub = getattr(mind, "push_hub", None) if mind is not None else None
    if push_hub is None or not scope:
        return
    try:
        import time as _time
        key = (scope, text)
        now = _time.monotonic()
        last = _APPROVAL_REMINDER_AT.get(key)
        if last is not None and now - last < _APPROVAL_REMINDER_MIN_INTERVAL:
            return
        if len(_APPROVAL_REMINDER_AT) >= 512:
            _APPROVAL_REMINDER_AT.pop(next(iter(_APPROVAL_REMINDER_AT)))
        _APPROVAL_REMINDER_AT[key] = now
        push_hub.push(scope, "approval", text, trigger=False)
    except Exception:
        pass
