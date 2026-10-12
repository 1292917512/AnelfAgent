"""视觉上下文提供者 — 各视觉源状态与最新画面注入 PFC volatile 层。

变化驱动注入：每个执行请求独立记录已见画面，同一请求内只补充变化帧；
首次读取最多附带三个活跃源，后续逐轮补齐，最多保留 200 个请求的轨迹。
无活跃源时不注入；静态推送按接收时间判活，不因长期未变化而消失。
注入轨迹（最近注入时间/来源/帧）记录于 provider 实例，供视觉页签展示。

Model Experience：
- 模型看到什么：有活跃视觉源时每轮看到"[视觉]"状态行（system 消息），
  首次读取或画面变化时看到带来源标签的图片（每轮最多三帧）；
- token 影响：状态行约 30-80 token；画面帧经 ensure_base64_report 压缩，
  同一请求不重复携带未变化的画面；
- 缓存影响：位于 volatile 尾部动态区（工具链后、exec_context 前），
  不触碰 stable/conversation 前缀缓存。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from core.context_provider import ContextMedia, ProviderSnapshot, context_provider
from core.tags import tag_label
from core.tool_context import request_trace

from .buffer import get_vision_buffer
from .watcher import get_vision_watcher


@context_provider(
    name="vision", priority=32, max_tokens=200,
    group="vision", inject_key="vision_context_inject",
)
class VisionProvider:
    """注入视觉源状态与最新画面（有活跃源时）。"""

    def __init__(self) -> None:
        self._seen: OrderedDict[tuple[str, str], dict[str, tuple[str, float]]] = OrderedDict()
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
            received = buffer.rings.get(src)
            last_seen = received[-1].captured_at if received else frame.captured_at
            if is_enabled(src) and time.time() - last_seen < 600:
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
        last_change = max((frame.captured_at for src, frame in buffer.latest_by_source.items() if src in active), default=0)
        if last_change:
            text += f"；最近画面变化于 {max(0, int(time.time() - last_change))} 秒前"

        identity = (scope, request_trace().get("request_id", ""))
        seen = self._seen.setdefault(identity, {})
        self._seen.move_to_end(identity)
        while len(self._seen) > 200:
            self._seen.popitem(last=False)
        frames = sorted(
            (frame for src, frame in buffer.latest_by_source.items() if src in active),
            key=lambda frame: frame.captured_at, reverse=True,
        )
        changed = [frame for frame in frames if seen.get(frame.source) != (frame.path, frame.captured_at)][:3]
        media = [ContextMedia.image(frame.path) for frame in changed]
        for frame in changed:
            seen[frame.source] = (frame.path, frame.captured_at)
            text += f"\n{frame.source} " + tag_label("media_type", "image") + tag_label("media_path", frame.path)
        if changed:
            self._last_media_inject = {
                "at": time.time(), "source": changed[0].source, "path": changed[0].path,
                "scope": scope, "sources": [frame.source for frame in changed],
            }
        self._last_text_inject_at = time.time()
        return ProviderSnapshot(content=text, media=media, ready=True)
