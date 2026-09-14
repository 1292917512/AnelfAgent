"""音频核心服务 — 解析调度（提供者链）与解析产物入库的统一入口。

Agent 的核心音频能力面：语音转写（ASR）与声纹提取经提供者注册表
按优先级链解析（首个 check_available 通过者承担）；解析产物经入库管线
（ingest.py：噪音过滤 → 声纹识别 → 落库）统一写入音频核心库（store.py），
与具体提供者实现解耦。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log

from .providers import KIND_ASR, KIND_VOICEPRINT, get_audio_registry
from .schemas import IngestPayload, IngestResult, SegmentIn
from .store import get_audio_store

_LOG_TAG = "音频"


class AudioNotConfigured(RuntimeError):
    """该类别无可用音频提供者（未配置/全部不可用）。"""


class AudioService:
    """音频核心服务（进程内单例，经 get_audio_service 获取）。"""

    async def transcribe(
        self, audio_path: str, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        """转写音频为分段结果（经 ASR 提供者优先级链）。

        Raises:
            AudioNotConfigured: 无可用 ASR 提供者。
        """
        provider = await get_audio_registry().resolve(KIND_ASR)
        if provider is None:
            raise AudioNotConfigured(
                "无可用 ASR 提供者（配置 FunASR 等转写服务后可用）"
            )
        segments = await provider.transcribe(audio_path, source_time=source_time)
        log(f"音频转写完成: {len(segments)} 段（提供者 {provider.name}）", "DEBUG", tag=_LOG_TAG)
        return segments

    async def speaker_embed(self, audio_path: str) -> Optional[List[float]]:
        """提取音频的声纹向量（经声纹提供者优先级链；无提供者返回 None）。"""
        provider = await get_audio_registry().resolve(KIND_VOICEPRINT)
        if provider is None:
            return None
        return await provider.embed(audio_path)

    async def ingest_payload(
        self, payload: IngestPayload,
    ) -> IngestResult:
        """解析结果入库（噪音过滤 → 声纹识别/建档 → 落库），返回批处理结果。"""
        from .ingest import ingest_payload as _ingest
        return await _ingest(payload, store=get_audio_store())

    async def transcribe_and_store(
        self,
        audio_path: str,
        *,
        source_file: str = "",
        device_source: str = "",
        source_time: str = "",
        ts_ns: Optional[int] = None,
    ) -> Dict[str, Any]:
        """转写并经入库管线存档（含声纹识别；实时音频接入的便捷路径）。"""
        segments = await self.transcribe(audio_path, source_time=source_time)
        result = await self.ingest_payload(IngestPayload(
            source_file=source_file or audio_path,
            device_source=device_source,
            ts=(ts_ns // 1_000_000_000) if ts_ns else None,
            segments=[SegmentIn(
                start_ms=int(s.get("start_ms", 0)),
                end_ms=int(s.get("end_ms", 0)),
                text=str(s.get("text", "")),
                vector=s.get("vector"),
                abs_start_ms=s.get("abs_start_ms"),
                abs_end_ms=s.get("abs_end_ms"),
            ) for s in segments],
        ))
        return {
            "segments": result.ingested,
            "skipped": result.skipped,
            "ids": [r.segment_id for r in result.results],
        }

    def status(self) -> Dict[str, Any]:
        """提供者注册状态（页签展示用）。"""
        registry = get_audio_registry()
        return {
            "providers": [{
                "name": p.name, "kind": p.kind, "priority": p.priority,
            } for p in registry.list()],
        }


_service: Optional[AudioService] = None


def get_audio_service() -> AudioService:
    """进程内单例。"""
    global _service
    if _service is None:
        _service = AudioService()
    return _service
