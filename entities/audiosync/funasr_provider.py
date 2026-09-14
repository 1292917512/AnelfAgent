"""FunASR 音频提供者 — 核心音频层的 ASR + 声纹提取组件。

把实体的 FunASR HTTP 客户端（client.py，实体主动拉取通道）封装为
核心音频注册表的两个提供者组件：ASR 转写与声纹向量提取（后者由
分段向量均值导出）。配置沿用同步组件配置键 audiosync_funasr_endpoint /
audiosync_funasr_timeout（用户既有的服务地址配置自然生效）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log


class FunAsrAsrProvider:
    """FunASR 转写提供者（ASR 类别）。"""

    name = "funasr"
    kind = "asr"
    priority = 10

    async def check_available(self) -> bool:
        from .client import is_configured
        return is_configured()

    async def transcribe(
        self, audio_path: str, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        from .client import transcribe
        return await transcribe(audio_path, source_time=source_time)


class FunAsrVoiceprintProvider:
    """FunASR 声纹提供者（分段向量均值作为整段声纹）。"""

    name = "funasr"
    kind = "voiceprint"
    priority = 10

    async def check_available(self) -> bool:
        from .client import is_configured
        return is_configured()

    async def embed(self, audio_path: str) -> Optional[List[float]]:
        from .client import transcribe
        try:
            segments = await transcribe(audio_path)
        except Exception as exc:
            log(f"FunASR 声纹提取失败: {exc}", "DEBUG", tag="音源同步")
            return None
        vectors = [s["vector"] for s in segments if s.get("vector")]
        if not vectors:
            return None
        dims = len(vectors[0])
        return [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]


def register_providers() -> None:
    """向核心音频注册表注册 FunASR 提供者（实体包导入时调用一次）。"""
    from entities._sdk import register_audio_provider
    register_audio_provider(FunAsrAsrProvider())
    register_audio_provider(FunAsrVoiceprintProvider())
