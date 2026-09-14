"""视觉上下文提供者 — 各视觉源状态与最新画面注入 PFC volatile 层。

变化驱动注入：仅当画面自上次注入后有变化才附带图片（静态画面只注入
状态行文本，不重复烧图片 token）；无活跃源时完全不注入（零成本）。
注入轨迹（最近注入时间/来源/帧）记录于 provider 实例，供视觉页签展示。

Model Experience：
- 模型看到什么：有活跃视觉源时每轮看到"[视觉]"状态行（system 消息），
  画面变化时视觉模型额外看到一条 user 角色画面消息（最新一帧）；
- token 影响：状态行约 30-80 token；画面帧经 ensure_base64_report 压缩，
  仅变化轮携带；
- 缓存影响：位于 volatile 尾部动态区（工具链后、exec_context 前），
  不触碰 stable/conversation 前缀缓存。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.context_provider import ContextMedia, ProviderSnapshot
from entities._sdk import context_provider

from .buffer import get_vision_buffer
from .watcher import get_vision_watcher


@context_provider(
    name="vision", priority=32, max_tokens=200,
    group="vision", inject_key="vision_context_inject",
)
class VisionProvider:
    """注入视觉源状态与最新画面（有活跃源时）。"""

    def __init__(self) -> None:
        self._last_injected_path: str = ""
        # 注入轨迹（页签"注入情况"展示）：最近一次文本/画面注入
        self._last_text_inject_at: float = 0.0
        self._last_media_inject: Dict[str, Any] = {}

    def injection_status(self) -> Dict[str, Any]:
        """注入情况快照（视觉页签展示用）。"""
        return {
            "last_text_inject_at": self._last_text_inject_at or None,
            "last_media_inject": self._last_media_inject or None,
        }

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        from .framework import is_enabled
        watcher = get_vision_watcher()
        buffer = get_vision_buffer()
        watching = [s for s in watcher.watching_sources() if is_enabled(s)]
        # 活跃源 = 监视中 或 近 10 分钟内有帧汇入（外部推送源的画面也有时效）；
        # 已停用源不参与注入
        active = set(watching)
        for src, frame in buffer.latest_by_source.items():
            if is_enabled(src) and time.time() - frame.captured_at < 600:
                active.add(src)
        if not active:
            return None

        parts: list[str] = []
        for src in sorted(active):
            src_frame = buffer.latest_by_source.get(src)
            if src_frame is None:
                parts.append(f"{src}: 监视中（尚未捕获到画面）")
                continue
            ago = max(0, int(time.time() - src_frame.captured_at))
            line = f"{src}: 画面捕获于 {ago} 秒前（{src_frame.width}x{src_frame.height}）"
            if src in watching:
                err = watcher.source_status(src).get("last_error")
                if err:
                    line += f"；最近捕获错误: {err}"
            parts.append(line)
        text = "[视觉] 活跃视觉源 —— " + "；".join(parts)
        if buffer.last_change_at:
            text += f"；最近画面变化于 {max(0, int(time.time() - buffer.last_change_at))} 秒前"

        # 变化驱动：全局最新帧自上次注入未变则不重复携带图片
        media = []
        latest = buffer.latest
        if latest is not None and latest.path != self._last_injected_path:
            media = [ContextMedia.image(latest.path)]
            self._last_injected_path = latest.path
            self._last_media_inject = {
                "at": time.time(), "source": latest.source, "path": latest.path,
            }
        self._last_text_inject_at = time.time()
        return ProviderSnapshot(content=text, media=media, ready=True)
