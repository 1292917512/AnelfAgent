"""跨频道感知：快照管理、语义召回、叙事构建。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import datetime
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from agent.config import get_mind_config
from agent.memory.memory_retriever import MemoryRetriever
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything

_PREVIEW_TAG_RE = re.compile(r"\[[^\]]*\]")


@dataclass
class ScopeActivity:
    """单个 scope 的近期活动快照。"""
    last_time: float = 0.0
    last_preview: str = ""
    adapter_key: str = ""


@dataclass
class ChannelSnapshot:
    """频道活动快照（轻量级，纯内存）。"""
    last_message_time: float = 0.0
    active_scopes: dict[str, ScopeActivity] = field(default_factory=dict)


def update_channel_snapshot(mind: Any, anything: "Everything") -> None:
    """记录频道活动快照，供跨频道感知使用。"""
    adapter_key = getattr(anything, "adapter_key", "") or ""
    if not adapter_key:
        return
    scope = mind._resolve_entity_scope(anything)
    if not scope:
        return

    raw = anything.get_text_content()[:80] if hasattr(anything, "get_text_content") else ""
    preview = _PREVIEW_TAG_RE.sub("", raw).strip()[:50]

    snap = mind._channel_snapshots.setdefault(adapter_key, ChannelSnapshot())
    now = time.time()
    snap.last_message_time = now

    activity = snap.active_scopes.setdefault(scope, ScopeActivity())
    activity.last_time = now
    activity.last_preview = preview
    activity.adapter_key = adapter_key

    mc = get_mind_config()
    window = mc.cross_channel_window_minutes * 60
    snap.active_scopes = {
        s: a for s, a in snap.active_scopes.items() if now - a.last_time < window
    }


def collect_channel_info(mind: Any) -> List[str]:
    """收集频道连接信息摘要，包含连接状态细节。"""
    channels: List[str] = []
    try:
        for key, ch in mind.channel_manager.list_channels().items():
            info = ch.get_status_info()
            status = info.get("status", "unknown")
            parts: List[str] = [info.get("name", key)]

            if info.get("bot_username"):
                parts.append(info["bot_username"])
            elif info.get("self_id"):
                parts.append(f"id:{info['self_id']}")

            detail = info.get("detail", "")
            if detail:
                parts.append(detail)
            else:
                ws_connected = info.get("ws_connected")
                if ws_connected is True:
                    parts.append("已连接")
                elif ws_connected is False:
                    parts.append("未连接")
                else:
                    parts.append(status)

            channels.append(f"{key}({', '.join(parts)})")
    except Exception as e:
        log(f"收集频道信息失败: {e}", "DEBUG", tag="思维")
    return channels


async def recall_cross_channel(
    mind: Any,
    query_conversation: List[Dict],
    current_adapter_key: str,
    current_scope: str,
    query_vec: Optional[List[float]] = None,
) -> Tuple[List[Dict], Set[str]]:
    """搜索其他频道的语义相关对话，返回 (注入消息列表, 已召回 scope 集合)。

    query_vec 为调用方预计算的查询向量（与语义召回共享一次 embedding），
    为 None 时内部按需自行计算。
    """
    recalled_scopes: Set[str] = set()
    mc = get_mind_config()
    if not mc.cross_channel_enabled or not mind.embedder.available:
        return [], recalled_scopes

    query = MemoryRetriever._extract_query(query_conversation)
    if not query or len(query) < 10:
        return [], recalled_scopes

    now = time.time()
    window = mc.cross_channel_window_minutes * 60
    other_scopes: List[Tuple[str, str, str]] = []
    for adapter_key, snap in mind._channel_snapshots.items():
        if adapter_key == current_adapter_key:
            continue
        if now - snap.last_message_time > window:
            continue
        for scope, activity in snap.active_scopes.items():
            if scope == current_scope or now - activity.last_time > window:
                continue
            scope_type = "group" if scope.startswith("group_") else "user"
            scope_id = scope.split("_", 1)[1] if "_" in scope else scope
            other_scopes.append((scope_type, scope_id, adapter_key))

    if not other_scopes:
        return [], recalled_scopes

    if query_vec is None:
        query_vec = await mind.embedder.embed_one(query)
    if not query_vec:
        return [], recalled_scopes

    try:
        _ = mind.conversation_data.router.sqlite
    except Exception:
        return [], recalled_scopes

    snippets: List[Dict[str, Any]] = []
    min_score = mc.cross_channel_recall_min_score
    max_results = mc.cross_channel_recall_max_results
    scan_limit = mc.cross_channel_recall_scan_limit

    for scope_type, scope_id, adapter_key in other_scopes[:5]:
        try:
            results = await mind.conversation_data.search_conversation_vector(
                scope_type, scope_id, query_vec,
                limit=2, skip_recent=0, min_score=min_score,
                scan_limit=scan_limit,
            )
            for r in results:
                full_scope = f"{scope_type}_{scope_id}"
                recalled_scopes.add(full_scope)
                snippets.append({
                    "adapter_key": adapter_key,
                    "scope": full_scope,
                    "content": r["content"][:300],
                    "role": r["role"],
                    "score": r.get("score", 0),
                    "ts_ns": r["ts_ns"],
                })
        except Exception:
            continue

    if not snippets:
        return [], recalled_scopes

    snippets.sort(key=lambda x: x["score"], reverse=True)
    snippets = snippets[:max_results]

    lines = ["[跨频道相关对话] 其他频道中与当前话题相关的近期对话："]
    for s in snippets:
        ts_sec = s["ts_ns"] // 1_000_000_000
        time_str = datetime.datetime.fromtimestamp(ts_sec).strftime("%H:%M")
        lines.append(
            f"  [{s['adapter_key']} · {s['scope']} · {time_str}] "
            f"{s['role']}: {s['content']}"
        )

    log(f"跨频道语义召回: {len(snippets)} 条 (阈值={min_score})", tag="思维")
    return [{"role": "system", "content": "\n".join(lines)}], recalled_scopes


def build_cross_channel_narrative(
    mind: Any,
    current_adapter_key: str,
    current_scope: str,
    already_recalled_scopes: Optional[Set[str]] = None,
) -> str:
    """生成跨频道近况叙述（已被语义召回覆盖的 scope 不重复出现）。"""
    mc = get_mind_config()
    if not mc.cross_channel_enabled:
        return ""

    now = time.time()
    window = mc.cross_channel_window_minutes * 60
    recalled = already_recalled_scopes or set()
    max_items = mc.cross_channel_narrative_max_items
    items: List[str] = []

    for adapter_key, snap in mind._channel_snapshots.items():
        if adapter_key == current_adapter_key:
            continue
        if now - snap.last_message_time > window:
            continue
        for scope, activity in snap.active_scopes.items():
            if scope == current_scope or scope in recalled:
                continue
            if now - activity.last_time > window:
                continue
            elapsed_min = int((now - activity.last_time) / 60)
            time_desc = f"{elapsed_min}分钟前" if elapsed_min > 0 else "刚刚"
            topic = f"聊到了「{activity.last_preview}」" if activity.last_preview else "有过对话"
            items.append(f"- {time_desc}在 {adapter_key}，{scope} 找你{topic}")
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break

    if not items:
        return ""

    return (
        "[你的近期对话]\n"
        "你正同时在多个平台与人交流。除了当前对话，你近期还和这些人互动过：\n"
        + "\n".join(items)
        + "\n如果发现和当前话题有关联，可以自然地联系起来思考。"
    )
