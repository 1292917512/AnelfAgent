"""音频上下文提供者 — 将声纹身份名单与未读动态注入 PFC volatile 层。

AI 每轮推理时即可感知"音频库里有哪些已确认说话人、谁的声纹关联到
哪个实体画像、有多少待确认新声纹、有多少条未读语音记录"，无需主动
调用工具就能自然地引用音频库。摘要走 store 内存缓存（写路径置脏），
稳态零 I/O。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from .store import get_audio_store


@context_provider(
    name="audio_status", priority=20, max_tokens=300,
    group="audio", inject_key="audio_context_inject",
)
class AudioStatusProvider:
    """注入音频库摘要：说话人名单 + 声纹-实体关联 + 待确认数 + 未读片段数。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        store = get_audio_store()
        summary = await store.summary()
        names = summary["confirmed_names"]
        bindings = summary.get("entity_bindings", [])
        pending = summary["pending_count"]
        unread = summary["unread_count"]
        if not names and not pending and not unread:
            return None

        lines = []
        if names:
            lines.append(f"[音频库] 已登记说话人 {len(names)} 人: {', '.join(names)}")
        if bindings:
            lines.append(
                f"[音频库] 声纹关联实体: {'；'.join(bindings)} —— "
                f"语音检索/通话标注按此归属实体画像")
        parts = []
        if pending:
            parts.append(f"待确认新声纹 {pending} 人")
        if unread:
            parts.append(f"未读语音记录 {unread} 条")
        if parts:
            hint = "；".join(parts)
            lines.append(
                f"[音频库] {hint} —— 可用 transcript_search / speaker_segments 检索，"
                f"speaker_update 确认归属")
        return ProviderSnapshot(content="\n".join(lines), ready=True)
