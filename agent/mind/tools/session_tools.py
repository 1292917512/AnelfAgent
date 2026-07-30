"""会话管理工具：多频道/多会话的发现与切换。

- list_sessions：列出活跃会话（未读消息 + 近期活动），供 AI 感知其他窗口动态。
- switch_session：将目标会话排入回复队列，以其独立隔离的上下文开启一轮 REPLY。

通过 deferred_tool 模式注册（group="session"），bootstrap 阶段激活。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

from agent.messages import parse_entity_scope
from core.log import log
from entities._sdk import deferred_tool

# ── 运行时引用（bootstrap 组装后通过 set_mind 注入）──

_pfc_ref: Any = None
_mind_ref: Any = None


def set_mind(mind: Any) -> None:
    """延迟注入 Mind 引用（bootstrap 组装完成后调用），同时获取 PFC。"""
    global _mind_ref, _pfc_ref
    _mind_ref = mind
    _pfc_ref = mind.pfc if mind is not None else None


def _current_scope() -> str:
    """当前思维会话的 scope（ContextVar，并行安全）。"""
    from agent.mind.tool_activation import ToolActivationManager
    scope = ToolActivationManager.current_scope()
    if scope and scope != "_global" and scope.startswith(("user_", "group_")):
        return scope
    return ""


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


def _snapshot_activities(mind: Any) -> Dict[str, Dict[str, Any]]:
    """汇总频道活动快照：scope → {adapter_key, last_time, last_preview}。"""
    result: Dict[str, Dict[str, Any]] = {}
    for adapter_key, snap in getattr(mind, "_channel_snapshots", {}).items():
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
    if not _pfc_ref or not _mind_ref:
        return json.dumps({"error": "系统未就绪"}, ensure_ascii=False)

    current = _current_scope()
    activities = _snapshot_activities(_mind_ref)
    now = time.time()

    sessions: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # 未读会话（待处理队列）
    for scope, _uid, _gid, preview in _pfc_ref.peek_all_tasks():
        seen.add(scope)
        act = activities.get(scope, {})
        sessions.append({
            "scope": scope,
            "label": _scope_label(scope),
            "channel": _pfc_ref.get_adapter_key(scope) or act.get("adapter_key", ""),
            "unread": _pfc_ref.get_unread_count(scope),
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
        "hint": "对有未读消息的会话可调用 switch_session(scope) 切换处理；"
                "不切换时你的回复默认发往当前会话。",
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
    if not _pfc_ref or not _mind_ref:
        return json.dumps({"error": "系统未就绪"}, ensure_ascii=False)

    scope = (scope or "").strip()
    scope_type, scope_adapter, _base_id, _session_id = parse_entity_scope(scope)
    if not scope_type:
        return json.dumps({
            "error": f"无效的 scope: '{scope}'",
            "hint": "格式应为 user_qq:123 / group_qq:456 / user_webui:u#chat_id，可先调用 list_sessions 获取",
        }, ensure_ascii=False)

    if scope == _current_scope():
        return json.dumps({
            "ok": True, "scope": scope,
            "hint": "已处于该会话，无需切换；直接回复即可",
        }, ensure_ascii=False)

    if scope in getattr(_mind_ref, "_active_scopes", set()):
        return json.dumps({
            "ok": True, "scope": scope,
            "hint": "该会话正在处理中，无需重复切换",
        }, ensure_ascii=False)

    # 路由信息：scope 自带 adapter 段优先，其次待处理队列，回退频道活动快照
    adapter_key = scope_adapter or _pfc_ref.get_adapter_key(scope)
    preview = ""
    if not adapter_key:
        act = _snapshot_activities(_mind_ref).get(scope, {})
        adapter_key = act.get("adapter_key", "")
        preview = act.get("last_preview", "")
    if not adapter_key:
        return json.dumps({
            "error": f"未知会话: '{scope}'（无路由信息）",
            "hint": "可先调用 list_sessions 查看可切换的会话",
        }, ensure_ascii=False)

    prompt = "[会话切换] 你主动切换到该会话处理消息"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请阅读上方消息后决定回复内容。"

    from agent.mind.tools.scheduler import enqueue_scope_reply
    enqueue_scope_reply(
        _pfc_ref, scope, adapter_key,
        preview or f"会话切换: {reason or '主动处理'}"[:60],
        prompt,
    )
    asyncio.create_task(_mind_ref.try_execute_mind())
    log(f"AI 切换会话: scope={scope} reason={reason}", tag="会话")

    return json.dumps({
        "ok": True,
        "scope": scope,
        "channel": adapter_key,
        "hint": "该会话已排入处理队列，将以独立上下文开启新回复；你当前的回复仍发往当前会话",
    }, ensure_ascii=False)
