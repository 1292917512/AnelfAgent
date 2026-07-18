"""延迟回复调度 — schedule_reply 工具。

通过 deferred_tool 模式注册（group="thinking"），bootstrap 阶段激活。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from entities._sdk import deferred_tool
from core.log import log

# ── 运行时引用（bootstrap 组装后通过 set_mind 注入）──

_pfc_ref: Any = None
_mind_ref: Any = None

_MAX_DELAY = 600


def set_mind(mind: Any) -> None:
    """延迟注入 Mind 引用（bootstrap 组装完成后调用），同时获取 PFC。"""
    global _mind_ref, _pfc_ref
    _mind_ref = mind
    _pfc_ref = mind.pfc


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description="延迟指定秒数后自动触发一轮新的对话回复，适用于需要等一会儿再主动联系用户的场景。",
)
async def schedule_reply(delay_seconds: int = 30, reason: str = "") -> str:
    """延迟指定秒数后自动触发一轮新的对话回复。

    Args:
        delay_seconds: 延迟秒数（1-600），默认30秒
        reason: 延迟原因，会作为提示注入下一轮上下文
    """
    if not _pfc_ref or not _mind_ref:
        return json.dumps({"error": "系统未就绪"}, ensure_ascii=False)

    delay = max(1, min(delay_seconds, _MAX_DELAY))

    reply_channel = getattr(_mind_ref, "_reply_adapter_key", "") or ""
    reply_target = ""
    for scope in getattr(_mind_ref, "_active_scopes", set()):
        if scope.startswith("user_"):
            reply_target = scope[5:]
        elif scope.startswith("group_"):
            reply_target = scope[6:]
        break

    if not reply_target:
        return json.dumps({"error": "无法确定回复目标"}, ensure_ascii=False)

    log(f"计划 {delay}s 后触发回复: target={reply_target} reason={reason}", tag="调度")
    asyncio.create_task(_delayed_reply(delay, reply_channel, reply_target, reason))

    return json.dumps({
        "ok": True,
        "delay_seconds": delay,
        "target": reply_target,
        "channel": reply_channel,
        "hint": f"{delay}秒后系统将自动触发一轮新的对话回复",
    }, ensure_ascii=False)


async def _delayed_reply(delay: int, channel: str, target: str, reason: str) -> None:
    """等待指定秒数后触发一轮 REPLY。"""
    await asyncio.sleep(delay)

    if not _pfc_ref or not _mind_ref:
        return

    prompt = f"[定时提醒] 你 {delay} 秒前设定了一个延迟回复"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请决定下一步操作。"

    _pfc_ref.add_temporary({"role": "system", "content": prompt})

    scope = f"user_{target}"
    _pfc_ref.pending_user.append(target)
    _pfc_ref._message_previews[scope] = f"定时回复: {reason or '延迟触发'}"
    if channel:
        _pfc_ref._task_adapter_keys[scope] = channel

    log(f"延迟 {delay}s 到期，触发回复: {scope}", tag="调度")
    asyncio.create_task(_mind_ref.try_execute_mind())
