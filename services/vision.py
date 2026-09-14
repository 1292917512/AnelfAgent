"""视觉服务门面 — web 层与 agent/vision 核心层之间的收口。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from agent.vision import all_sources, get_vision_buffer, get_vision_watcher

_PUSH_MAX_BYTES = 20 * 1024 * 1024
_PUSH_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class VisionServiceFacade:
    """视觉页签的数据聚合（监视状态 + 源清单 + 帧推送 + 注入情况）。"""

    def status(self) -> Dict[str, Any]:
        buffer = get_vision_buffer()
        return {
            **get_vision_watcher().status(),
            "latest": {
                "path": buffer.latest.path, "source": buffer.latest.source,
                "captured_at": buffer.latest.captured_at,
            } if buffer.latest else None,
            "last_change_at": buffer.last_change_at or None,
            "injection": self.injection_status(),
        }

    def injection_status(self) -> Dict[str, Any]:
        """画面注入情况（页签展示：开关 + 最近文本/画面注入轨迹）。"""
        from core.context_provider import ContextProviderRegistry
        for meta in ContextProviderRegistry.get_all():
            if meta.group == "vision" and meta.instance is not None:
                instance = meta.instance
                extra = {}
                if hasattr(instance, "injection_status"):
                    extra = instance.injection_status()
                return {
                    "provider": meta.name,
                    "active": ContextProviderRegistry._is_active(meta),
                    **extra,
                }
        return {"provider": "", "active": False}

    def sources(self) -> Dict[str, Any]:
        from agent.vision.framework import is_enabled
        watcher = get_vision_watcher()
        return {
            "sources": [{
                "key": s.key, "display_name": s.display_name,
                "description": s.description,
                "pollable": s.poll_interval > 0 and s.can_capture,
                "can_capture": s.can_capture,
                "enabled": is_enabled(s.key),
                "watching": watcher.watching(s.key),
            } for s in all_sources()],
        }

    def latest_frame(self, source: str = "") -> Optional[str]:
        """最新一帧的文件路径（source 指定源；不存在/文件已删返回 None）。"""
        buffer = get_vision_buffer()
        frame = buffer.latest_by_source.get(source) if source else buffer.latest
        if frame is None or not os.path.isfile(frame.path):
            return None
        return frame.path

    def capabilities(self) -> Dict[str, Any]:
        """视觉能力（理解/图生成/图编辑/视频）的提供者状态与生效优先级链。"""
        from agent.vision.capabilities import VISUAL_CAPABILITIES, get_visual_router
        return get_visual_router().status(list(VISUAL_CAPABILITIES))

    async def watch(self, action: str, source: str, interval: float = 0) -> Dict[str, Any]:
        """监视开关与源启停（页签控制；与 vision_watch/vision_source_set 工具同语义）。"""
        from agent.vision.framework import is_enabled, set_enabled
        watcher = get_vision_watcher()
        if action == "enable":
            set_enabled(source, True)
            return {"watching": watcher.watching_sources(), "enabled": True}
        if action == "disable":
            set_enabled(source, False)
            if watcher.watching(source):
                await watcher.stop(source)
            return {"watching": watcher.watching_sources(), "enabled": False}
        if action == "start":
            if not is_enabled(source):
                raise ValueError(f"视觉源已停用: {source}（先激活再监视）")
            if interval > 0:
                from core.config import ConfigManager
                ConfigManager.set("vision_watch_interval_s", interval)
                ConfigManager.save()
            error = await watcher.start(source)
            if error:
                raise ValueError(error)
        elif action == "stop":
            await watcher.stop(source)
        elif action != "status":
            raise ValueError(f"未知 action: {action}")
        return {"watching": watcher.watching_sources()}

    async def push_frame(
        self, source: str, image_base64: str, mime_type: str,
        captured_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """外部视觉源推送一帧（base64 图片 → 缓冲统一入口）。

        Raises:
            ValueError: 非法 source 名 / 不支持的类型 / base64 非法。
            OverflowError: 图片为空或超过上限。
        """
        source = source.strip()
        if not source or ".." in source or "/" in source:
            raise ValueError("非法 source 名")
        from agent.vision.framework import is_enabled
        if not is_enabled(f"external:{source}"):
            raise ValueError(f"外部视觉源已停用: {source}")
        if mime_type not in _PUSH_EXTS:
            raise ValueError(f"不支持的图片类型: {mime_type}")
        try:
            data = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image_base64 不是合法的 base64") from None
        if not data or len(data) > _PUSH_MAX_BYTES:
            raise OverflowError("图片为空或超过 20MB 上限")

        from core.path import ConfigPaths
        out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / "vision"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{int(time.time() * 1000)}_{source}{_PUSH_EXTS[mime_type]}"

        def _write() -> None:
            dest.write_bytes(data)

        await asyncio.to_thread(_write)
        frame, changed = await get_vision_buffer().ingest(
            str(dest), f"external:{source}",
            captured_at=captured_at or time.time(),
        )
        return {
            "ok": True, "path": frame.path, "changed": changed,
            "source": frame.source,
        }
