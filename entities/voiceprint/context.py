"""音源库上下文提供者 — 将说话人名单与未读动态注入 PFC volatile 层。

AI 每轮推理时即可感知"音源库里有哪些已确认说话人、有多少待确认新声纹、
有多少条未读语音记录"，无需主动调用工具就能自然地引用音源库。
摘要走 store 内存缓存（写路径置脏），稳态零 I/O。
"""

from __future__ import annotations

from typing import Optional

from core.config import get_config_bool
from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from .store import get_voiceprint_store


@context_provider(name="voiceprint_status", priority=30, max_tokens=300)
class VoiceprintStatusProvider:
    """注入音源库摘要：已确认说话人名单 + 待确认数 + 未读片段数。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        if not get_config_bool("voiceprint_context_inject", True):
            return None
        store = get_voiceprint_store()
        summary = await store.summary()
        names = summary["confirmed_names"]
        pending = summary["pending_count"]
        unread = summary["unread_count"]
        if not names and not pending and not unread:
            return None

        lines = []
        if names:
            lines.append(f"[音源库] 已登记说话人 {len(names)} 人: {', '.join(names)}")
        parts = []
        if pending:
            parts.append(f"待确认新声纹 {pending} 人")
        if unread:
            parts.append(f"未读语音记录 {unread} 条")
        if parts:
            hint = "；".join(parts)
            lines.append(
                f"[音源库] {hint} —— 可用 transcript_search / speaker_segments 检索，"
                f"speaker_update 确认归属")
        return ProviderSnapshot(content="\n".join(lines), ready=True)
