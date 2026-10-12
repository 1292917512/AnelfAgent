"""人脸上下文提供者 — 将人物名单与画面动态注入 PFC volatile 层。

AI 每轮推理时即可感知"人脸库里有哪些已确认人物、谁的脸关联到哪个
实体画像、有多少待确认新人物、近期画面里出现过谁"，无需主动调用工具
就能自然地引用人脸识别结果。摘要走 store 内存缓存（写路径置脏），
稳态零 I/O；近期在场行有独立短 TTL 缓存（注入频率高于事件频率）。

Model Experience：
- 模型看到人物编号、face_scope 绑定与近期出现事实；同名人物按身份区分。
- 人物与绑定合并为一行，总预算仍为 300 token，待处理计数优先。
- 内容在工具链后的动态区更新，不改稳定前缀；名单走写失效缓存。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from core.context_provider import ProviderSnapshot, context_provider
from core.tags import tag_label

from .store import get_face_store

# 近期在场行的缓存 TTL（秒）与时间窗（秒）
_RECENT_CACHE_TTL = 60.0
_RECENT_WINDOW_S = 3600.0
_recent_cache: Tuple[float, List[str]] = (0.0, [])


async def _recent_present_lines() -> List[str]:
    """最近时间窗内出现的已命名人物行（短 TTL 缓存，防每轮查库）。"""
    global _recent_cache
    cached_at, cached = _recent_cache
    now = time.monotonic()
    if now - cached_at < _RECENT_CACHE_TTL:
        return cached
    store = get_face_store()
    window_ns = int(_RECENT_WINDOW_S * 1_000_000_000)
    result = await store.list_events(
        from_ns=time.time_ns() - window_ns, limit=50)
    seen: Dict[tuple[str, str], str] = {}
    for event in result["items"]:
        for face in event.get("faces", []):
            name = str(face.get("person_name") or "")
            identity = str(face.get("person_id") or face.get("person_key") or name)
            scope = str(face.get("entity_scope") or "")
            if name:
                seen.setdefault((identity, scope), name)
    lines: List[str] = []
    if seen:
        rendered = "、".join(
            name + (tag_label("face_scope", scope) if scope else "")
            for (_, scope), name in seen.items())
        lines.append(f"[人脸] 最近 {int(_RECENT_WINDOW_S / 60)} 分钟画面中出现: {rendered}")
    _recent_cache = (now, lines)
    return lines


@context_provider(
    name="face_status", priority=21, max_tokens=300,
    group="vision", inject_key="face_context_inject",
)
class FaceStatusProvider:
    """注入人脸库摘要：人物名单 + 人脸-实体关联 + 待确认数 + 未读事件数。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        store = get_face_store()
        summary = await store.summary()
        identities = summary["identities"]
        pending = summary["pending_count"]
        unread = summary["unread_count"]
        recent = await _recent_present_lines()
        if not identities and not pending and not unread and not recent:
            return None

        lines: List[str] = []
        parts = []
        if pending:
            parts.append(f"待确认新人物 {pending} 人")
        if unread:
            parts.append(f"未读出现事件 {unread} 条")
        if parts:
            lines.append(
                f"[人脸库] {'；'.join(parts)} —— 可用 face_events 查看时间线、"
                f"face_identify 识别图片，face_update 确认归属")
        lines.extend(recent)
        if identities:
            roster = []
            for identity in identities:
                label = identity["name"] + (f"({identity['role']})" if identity["role"] else "")
                binding = tag_label("face_scope", identity["entity_scope"]) if identity["entity_scope"] else ""
                roster.append(f"#{identity['id']} {label}{binding}")
            lines.append("[人脸库] 近期登记身份（不代表当前在场）: " + "；".join(roster))
        return ProviderSnapshot(content="\n".join(lines), ready=True)
