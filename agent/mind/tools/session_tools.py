"""会话管理工具：多频道/多会话的发现与切换。

- list_sessions：列出活跃会话（未读消息 + 近期活动），供 AI 感知其他窗口动态。
- switch_session：将目标会话排入回复队列，以其独立隔离的上下文开启一轮 REPLY。

模型可见：get_conversation_diagnostics 按需返回消息摘录、耗时和执行回执。
Token 影响：仅工具查询时提供有界结果，不自动注入诊断或调用模型总结。
缓存影响：不改变稳定前缀、已读水位或实时会话内容。

通过 deferred_tool 模式注册（group="session"），bootstrap 阶段激活。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Dict, List

from agent.messages import is_conversation_scope, parse_entity_scope
from core.entity import EntityRegistry
from core.log import log
from core.tool_errors import ErrorCause, tool_error
from core.tool_registry import deferred_tool

from .ports import mind_port

if TYPE_CHECKING:
    from agent.mind.work_memory import WorkMemory

EntityRegistry.register_group_order("session", 71)


def _current_scope() -> str:
    """当前思维会话的 scope（ContextVar，并行安全）。"""
    from agent.mind.tool_activation import ToolActivationManager
    scope = ToolActivationManager.current_scope()
    return scope if is_conversation_scope(scope) else ""


def _system_not_ready() -> str:
    """会话系统未就绪的统一错误。"""
    return tool_error(
        "系统未就绪",
        cause=ErrorCause.STATE, retryable=True,
        hint="系统组件尚未完成初始化，请稍后重试",
    )


def _scope_label(scope: str) -> str:
    """生成 scope 的展示标签（频道 + 私聊/群聊 + 基 id + 子会话）。"""
    scope_type, adapter, base_id, session_id = parse_entity_scope(scope)
    if not scope_type:
        return scope
    kind = "群聊" if scope_type == "group" else "私聊"
    label = f"{adapter} {kind} {base_id}" if adapter else f"{kind} {base_id}"
    if session_id:
        label += f"#{session_id}"
    return label


@deferred_tool(
    group="session", tags=["core"], source="mind.session", concurrency_safe=True,
    description="查看自己当前及近期执行的实际耗时、模型首响应/缓存用量、工具结果与投递回执，"
                "附会话消息摘录。用于判断卡在哪一步、核对是否已执行/已投递、避免重复操作。"
                "只读有界内存，不触发召回或数据库查询，不改变消息已读状态。",
)
async def get_conversation_diagnostics(
    scope: str = "", turn_id: str = "", turns: int = 2, steps: int = 8, messages: int = 6,
) -> str:
    """按需诊断当前或指定会话，不必每轮调用。

    Args:
        scope: 默认当前会话；指定完整 scope 可查其他会话，* 查看近期全部会话。
        turn_id: 可选轮次 ID，精确查看某轮执行；无会话时默认当前执行轮次。
        turns: 最近轮数，1–10。
        steps: 每轮返回最近步骤数，1–80；运行中的步骤另列 active_steps。
        messages: 每个会话的最近消息摘录条数，1–20，按提交顺序。
    """
    if not mind_port.bound:
        return _system_not_ready()
    from agent.mind.diagnostic_query import recent_diagnostics
    from core.activity import current_activity_id
    from core.tool_errors import error_from_exception

    selected = "" if scope == "*" else scope or _current_scope()
    selected_turn = turn_id or (current_activity_id.get() if not selected and scope != "*" else "")
    try:
        return json.dumps(recent_diagnostics(
            mind_port.get().conversation_data.router.sqlite, scope=selected, turn_id=selected_turn,
            limit=turns, steps=steps, messages=messages,
        ), ensure_ascii=False)
    except Exception as exc:
        return error_from_exception(exc, action="查询会话执行诊断")


def _snapshot_activities(work_memory: WorkMemory) -> Dict[str, Dict[str, Any]]:
    """汇总频道活动快照：scope → {adapter_key, last_time, last_preview}。"""
    result: Dict[str, Dict[str, Any]] = {}
    for adapter_key, snap in work_memory.channel_snapshots.items():
        for scope, activity in snap.active_scopes.items():
            result[scope] = {
                "adapter_key": activity.adapter_key or adapter_key,
                "last_time": activity.last_time,
                "last_preview": activity.last_preview,
            }
    return result


@deferred_tool(
    group="session", tags=["always"], source="mind.session",
    description="列出所有活跃会话窗口（各频道的私聊/群聊/子会话），含未读消息数与最近消息预览，"
                "用于感知其他会话动态后决定是否 switch_session 切换处理。",
)
async def list_sessions() -> str:
    """列出所有活跃会话窗口及其未读状态。"""
    if not mind_port.bound:
        return _system_not_ready()
    mind = mind_port.get()

    current = _current_scope()
    activities = _snapshot_activities(mind.pfc.work_memory)
    now = time.time()

    sessions: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # 未读会话（待处理队列）
    for scope, _uid, _gid, preview in mind.pfc.peek_all_tasks():
        seen.add(scope)
        act = activities.get(scope, {})
        sessions.append({
            "scope": scope,
            "label": _scope_label(scope),
            "channel": mind.pfc.get_adapter_key(scope) or act.get("adapter_key", ""),
            "unread": mind.pfc.get_unread_count(scope),
            "preview": preview or act.get("last_preview", ""),
            "is_current": scope == current,
        })

    # 近期活跃但无未读的会话（快照窗口内）
    window = 0
    try:
        from agent.config import get_mind_config
        window = get_mind_config().cross_channel_window_minutes * 60
    except Exception:
        log("list_sessions 异常已忽略", "DEBUG")
    for scope, act in activities.items():
        if scope in seen:
            continue
        last_time = float(act.get("last_time", 0) or 0)
        if window > 0 and now - last_time > window:
            continue
        sessions.append({
            "scope": scope,
            "label": _scope_label(scope),
            "channel": act.get("adapter_key", ""),
            "unread": 0,
            "preview": act.get("last_preview", ""),
            "last_active_minutes_ago": max(0, int((now - last_time) / 60)),
            "is_current": scope == current,
        })

    return json.dumps({
        "current_scope": current,
        "session_count": len(sessions),
        "sessions": sessions,
        "space": "conversation" if current else "internal",
        "hint": "可调用 switch_session(scope) 打开目标会话的独立回复，或用 send_message 显式指定收件人。"
                + ("当前正文的默认目标仍是 current_scope，是否送达以回执为准。" if current
                   else "当前处于内部空间，没有默认频道或收件人，普通正文不会投递。"),
    }, ensure_ascii=False)


@deferred_tool(
    group="session", tags=["always"], source="mind.session",
    description="切换到另一个会话窗口处理其消息。目标会话将以独立上下文开启一轮新回复；"
                "你当前的回复仍发往当前会话。scope 格式：user_qq:123 / group_qq:456 / user_webui:u#chat_id。",
)
async def switch_session(scope: str, reason: str = "") -> str:
    """切换到指定会话窗口处理其未读消息。

    Args:
        scope: 目标会话标识（user_qq:123 / group_qq:456 / user_webui:u#chat_id，可由 list_sessions 获取）
        reason: 切换原因（可选，会作为提示注入目标会话的新一轮上下文）
    """
    if not mind_port.bound:
        return _system_not_ready()
    mind = mind_port.get()

    scope = (scope or "").strip()
    scope_type, scope_adapter, _base_id, _session_id = parse_entity_scope(scope)
    if not scope_type or not scope_adapter:
        return json.dumps({
            "error": f"无效的 scope: '{scope}'（需含频道前缀）",
            "hint": "格式应为 user_qq:123 / group_qq:456 / user_webui:u#chat_id，可先调用 list_sessions 获取",
        }, ensure_ascii=False)

    if scope == _current_scope():
        return json.dumps({
            "ok": True, "scope": scope,
            "hint": "已处于该会话，无需切换；直接回复即可",
        }, ensure_ascii=False)

    if scope in getattr(mind, "_active_scopes", set()):
        return json.dumps({
            "ok": True, "scope": scope,
            "hint": "该会话正在处理中，无需重复切换",
        }, ensure_ascii=False)

    preview = _snapshot_activities(mind.pfc.work_memory).get(scope, {}).get("last_preview", "")

    prompt = "[会话切换] 你主动切换到该会话处理消息"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请阅读上方消息后决定回复内容。"

    from agent.mind.tools.scheduler import enqueue_scope_reply
    await enqueue_scope_reply(
        mind.pfc, scope, scope_adapter,
        preview or f"会话切换: {reason or '主动处理'}"[:60],
        prompt,
    )
    asyncio.create_task(mind.try_execute_mind())
    log(f"AI 切换会话: scope={scope} reason={reason}", tag="会话")

    return json.dumps({
        "ok": True,
        "scope": scope,
        "channel": scope_adapter,
        "hint": "该会话已排入处理队列，将以独立上下文开启新回复；你当前的回复仍发往当前会话",
    }, ensure_ascii=False)
