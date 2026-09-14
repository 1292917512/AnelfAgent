"""小度播报 TTS — edge-tts 合成与文件缓存。

小度的 DLNA 客户端不支持流式/无 Content-Length 的音频（会"一卡一卡
播不完"），必须等 mp3 完整落盘后再下发 URL。缓存文件名取
内容 hash（同文本复用），超龄文件合成时顺带清理。
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from core.log import log

_LOG_TAG = "智能家居"

_CACHE_TTL_SECONDS = 3600.0
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tts_cache")


class TtsSynthesizer:
    """edge-tts 语音合成器（引擎接口隔离：替换引擎只需改 synthesize）。"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    async def synthesize(self, text: str) -> str:
        """合成文本为 mp3 文件并返回路径（缓存命中直接复用）。"""
        import edge_tts

        key = hashlib.sha256(f"{self._voice}\n{text}".encode()).hexdigest()[:24]
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = os.path.join(_CACHE_DIR, f"{key}.mp3")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        communicate = edge_tts.Communicate(text, self._voice)
        await communicate.save(path)
        self._sweep()
        return path

    @staticmethod
    def cache_dir() -> str:
        """缓存目录（音频服务的内容根）。"""
        return _CACHE_DIR

    @staticmethod
    def _sweep() -> None:
        """清理超龄缓存文件（合成时顺带执行，失败仅记日志）。"""
        try:
            now = time.time()
            for name in os.listdir(_CACHE_DIR):
                path = os.path.join(_CACHE_DIR, name)
                if now - os.path.getmtime(path) > _CACHE_TTL_SECONDS:
                    os.remove(path)
        except OSError as exc:
            log(f"TTS 缓存清理异常: {exc}", "DEBUG", tag=_LOG_TAG)


_SYNTHESIZER: Optional[TtsSynthesizer] = None


def get_synthesizer(voice: str = "zh-CN-XiaoxiaoNeural") -> TtsSynthesizer:
    """合成器单例（voice 与现实例不一致时重建）。"""
    global _SYNTHESIZER
    if _SYNTHESIZER is None or _SYNTHESIZER._voice != voice:
        _SYNTHESIZER = TtsSynthesizer(voice)
    return _SYNTHESIZER
