"""音频服务门面 — web 层与 agent/audio 核心层之间的收口。

音频页签与核心路由 /api/audio 的数据聚合：提供者链状态、语音会话配置、
音频库（片段/声纹身份/录制单元）的完整管理面、上下文注入情况。
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from agent.audio import (
    KIND_ASR,
    KIND_VOICEPRINT,
    AudioNotConfigured,
    get_audio_registry,
    get_audio_store,
    matcher,
)
from agent.audio.listen import ListenError  # noqa: F401  # 门面再导出（web 层归因用）
from agent.audio.schemas import (  # noqa: F401  # 门面再导出（web 层请求模型）
    ConfirmRequest,
    EnrollRequest,
    IdentifyCandidate,
    ImportRequest,
    MergeRequest,
    SegmentAddRequest,
    SegmentMergeRequest,
    SegmentSplitRequest,
    SegmentUpdateRequest,
    SpeakerBindRequest,
    SpeakerUpdateRequest,
    TranscriptReplaceRequest,
    VectorIdentifyRequest,
)
from core.config import ConfigManager
from core.log import log

_LOG_TAG = "音频"


class AudioServiceFacade:
    """音频页签的数据聚合与音频库管理面。"""

    # ------------------------------------------------------------------
    # 状态总览
    # ------------------------------------------------------------------

    async def status(self) -> Dict[str, Any]:
        registry = get_audio_registry()
        providers: List[Dict[str, Any]] = []
        for p in registry.list():
            try:
                available = await p.check_available()
            except Exception:
                available = False
            providers.append({
                "name": p.name, "kind": p.kind, "priority": p.priority,
                "available": available,
            })

        # 语音会话（agent/voice）配置快照
        voice_keys = (
            "voice_turn_detector", "voice_silence_ms", "voice_min_utterance_ms",
            "voice_max_utterance_s", "voice_vad_floor_min",
            "voice_smart_turn_threshold", "voice_denoise", "voice_agc",
        )
        voice_config = {k: ConfigManager.get(k) for k in voice_keys}

        store = get_audio_store()
        try:
            stats = await store.stats()
        except Exception as exc:
            log(f"音频库统计失败: {exc}", "DEBUG", tag=_LOG_TAG)
            stats = {}
        # TTS 提供者链（语音合成）
        tts_providers: List[Dict[str, Any]] = []
        from agent.tts import get_tts_registry
        for p in get_tts_registry().list():
            try:
                available = await p.check_available()
            except Exception:
                available = False
            tts_providers.append({
                "name": p.name, "priority": p.priority, "available": available,
            })

        # 实时语音会话状态
        from agent.realtime import get_realtime_engine
        realtime = {
            "enabled": ConfigManager.get("realtime_enabled", True),
            "mode": ConfigManager.get("realtime_mode", "cascade"),
            **get_realtime_engine().status(),
        }

        return {
            "providers": providers,
            "asr_available": any(p["available"] for p in providers if p["kind"] == KIND_ASR),
            "voiceprint_available": any(
                p["available"] for p in providers if p["kind"] == KIND_VOICEPRINT),
            "tts_providers": tts_providers,
            "tts_available": any(p["available"] for p in tts_providers),
            "realtime": realtime,
            "voice_config": voice_config,
            "library": stats,
            "injection": self.injection_status(),
        }

    def injection_status(self) -> Dict[str, Any]:
        """音频上下文注入情况（页签展示：开关 + 最近注入）。"""
        from core.context_provider import ContextProviderRegistry
        for meta in ContextProviderRegistry.get_all():
            if meta.group == "audio":
                return {
                    "provider": meta.name,
                    "active": ContextProviderRegistry._is_active(meta),
                }
        return {"provider": "", "active": False}

    def sound_capabilities(self) -> Dict[str, Any]:
        """声音能力（语音合成/音色管理/音乐生成）的提供者状态与生效优先级链。"""
        from agent.audio.capabilities import SOUND_CAPABILITIES, get_sound_router
        return get_sound_router().status(list(SOUND_CAPABILITIES))

    async def analyze_file(self, path: str) -> Dict[str, Any]:
        """对 uploads 内音频文件转写并经入库管线存档（含声纹识别）。

        Raises:
            FileNotFoundError: 文件不在上传目录内或不存在（404）。
            AudioNotConfigured: 无可用 ASR 提供者（503）。
        """
        from agent.audio import get_audio_service
        from core.path import ConfigPaths
        upload_root = os.path.realpath(str(ConfigPaths.UPLOAD_DIR))
        real = os.path.realpath(path)
        if not real.startswith(upload_root + os.sep) or not os.path.isfile(real):
            raise FileNotFoundError(f"文件不在上传目录内或不存在: {path}")
        return await get_audio_service().transcribe_and_store(real)

    async def transcribe_upload(
        self, filename: str, content: bytes, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        """把上传音频落地临时文件并经 ASR 链转写。

        Raises:
            AudioNotConfigured: 无可用 ASR 提供者（503）。
        """
        from agent.audio import get_audio_service
        if await get_audio_registry().resolve(KIND_ASR) is None:
            raise AudioNotConfigured("无可用 ASR 提供者（配置 FunASR 等转写服务后可用）")
        suffix = os.path.splitext(filename or "audio.wav")[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(prefix="audio_upload_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            return await get_audio_service().transcribe(
                tmp_path, source_time=source_time)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 声纹身份（说话人）
    # ------------------------------------------------------------------

    async def list_speakers(
        self, status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        return await get_audio_store().list_speakers(
            status=status, keyword=keyword, limit=min(limit, 200), offset=offset)

    async def speaker_detail(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        store = get_audio_store()
        speaker = await store.get_speaker(speaker_id)
        if not speaker:
            return None
        samples = await store.list_samples(speaker_id)
        recent = await store.list_segments(speaker_id=speaker_id, limit=5)
        return {
            "speaker": speaker,
            "effective_threshold": matcher.effective_threshold(speaker),
            "samples": samples,
            "recent_segments": recent["items"],
        }

    async def update_speaker(self, speaker_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        fields = {k: v for k, v in fields.items() if v is not None}
        return await get_audio_store().update_speaker(speaker_id, **fields)

    async def bind_speaker(self, speaker_id: int, entity_scope: str) -> Optional[Dict[str, Any]]:
        return await get_audio_store().bind_entity(speaker_id, entity_scope)

    async def speakers_for_entity(self, entity_scope: str) -> List[Dict[str, Any]]:
        return await get_audio_store().speakers_for_entity(entity_scope)

    async def confirm_speaker(self, speaker_id: int, name: str, role: str = "") -> Any:
        return await matcher.confirm(get_audio_store(), speaker_id, name, role=role)

    async def delete_speaker(self, speaker_id: int) -> Any:
        return await get_audio_store().delete_speaker(speaker_id)

    async def merge_speakers(self, source_id: int, target_id: int) -> Dict[str, Any]:
        return await matcher.merge(get_audio_store(), source_id, target_id)

    async def enroll_speaker(self, req: Any) -> Dict[str, Any]:
        return await matcher.enroll(
            get_audio_store(), req.name, req.vector, role=req.role,
            notes=req.notes, device_source=req.device_source,
            entity_scope=getattr(req, "entity_scope", ""))

    async def import_speakers(self, req: Any) -> Dict[str, Any]:
        store = get_audio_store()
        imported: List[Dict[str, Any]] = []
        for item in req.items:
            speaker = await matcher.enroll(
                store, item.name, item.vectors[0], role=item.role, notes=item.notes,
                source="import")
            for vec in item.vectors[1:]:
                await store.add_sample(
                    int(speaker["id"]), vec, source="import",
                    max_samples=matcher.max_samples_per_speaker())
            imported.append({"id": speaker["id"], "speaker_key": speaker["speaker_key"],
                             "name": speaker["name"], "samples": len(item.vectors)})
        return {"imported": imported, "total": len(imported)}

    async def enroll_audio(self, filename: str, content: bytes, name: str,
                           role: str = "", notes: str = "") -> Dict[str, Any]:
        segments = await self.transcribe_upload(filename, content)
        vectors = [s["vector"] for s in segments if s.get("vector")]
        if not vectors:
            raise RuntimeError("音频中未提取到有效声纹")
        store = get_audio_store()
        speaker = await matcher.enroll(
            store, name, vectors[0], role=role, notes=notes, device_source=filename)
        for vec in vectors[1:matcher.max_samples_per_speaker()]:
            await store.add_sample(int(speaker["id"]), vec, source="enroll",
                                   max_samples=matcher.max_samples_per_speaker())
        return {"speaker": speaker, "samples_enrolled": len(vectors)}

    async def prune_speakers(self, include_with_samples: bool) -> Dict[str, Any]:
        deleted = await get_audio_store().prune_pending_speakers(
            include_with_samples=include_with_samples)
        return {"pruned": len(deleted), "include_with_samples": include_with_samples,
                "speakers": deleted}

    async def similarity_map(self, status: str, neighbors: int, threshold: float) -> Dict[str, Any]:
        from agent.audio.consolidate import similarity_map
        return await similarity_map(
            get_audio_store(), status=status, neighbors=neighbors,
            threshold=threshold if 0.0 < threshold < 1.0 else None)

    async def consolidate_speakers(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from agent.audio.consolidate import consolidate
        threshold = payload.get("threshold")
        return await consolidate(
            get_audio_store(),
            threshold=float(threshold) if isinstance(threshold, (int, float))
                      and 0.0 < float(threshold) < 1.0 else None,
            dry_run=bool(payload.get("dry_run", True)),
            status="" if payload.get("include_confirmed") else "pending",
            prune_insignificant=bool(payload.get("prune_insignificant", False)),
        )

    async def delete_sample(self, sample_id: int) -> bool:
        return await get_audio_store().delete_sample(sample_id)

    # ------------------------------------------------------------------
    # 声纹识别
    # ------------------------------------------------------------------

    async def identify_vector(self, vector: List[float], top_k: int) -> List[Dict[str, Any]]:
        return await matcher.match_vector(get_audio_store(), vector, top_k=top_k)

    async def identify_audio(self, filename: str, content: bytes, *,
                             ingest: bool, source_time: str = "") -> Dict[str, Any]:
        segments = await self.transcribe_upload(filename, content, source_time=source_time)
        store = get_audio_store()
        if ingest:
            from agent.audio.ingest import ingest_payload
            from agent.audio.schemas import IngestPayload, SegmentIn
            ingest_result = await ingest_payload(IngestPayload(
                source_file=filename,
                segments=[SegmentIn(
                    start_ms=s["start_ms"], end_ms=s["end_ms"],
                    text=s["text"], vector=s["vector"],
                    abs_start_ms=s.get("abs_start_ms"),
                    abs_end_ms=s.get("abs_end_ms"),
                ) for s in segments],
            ), store=store)
            return {"ingested": True, **ingest_result.model_dump()}
        items = []
        for seg in segments:
            candidates = (
                await matcher.match_vector(store, seg["vector"]) if seg.get("vector") else [])
            items.append({
                "start_ms": seg["start_ms"],
                "end_ms": seg["end_ms"],
                "text": seg["text"],
                "candidates": candidates,
            })
        return {"ingested": False, "segments": items}

    # ------------------------------------------------------------------
    # 语音片段（时间线 / 检索 / 编辑）
    # ------------------------------------------------------------------

    async def list_segments(
        self, *, speaker_id: Optional[int] = None, recording_path: str = "",
        time_from: str = "", time_to: str = "", q: str = "",
        unread_only: bool = False, limit: int = 20, offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        from agent.audio.store import parse_time_ns
        store = get_audio_store()
        from_ns = parse_time_ns(time_from)
        to_ns = parse_time_ns(time_to)
        if q.strip():
            query_vec = None
            try:
                from agent.memory.embedding import get_embedder
                query_vec = await get_embedder("text").embed_query(q)
            except Exception as exc:
                log(f"查询向量化失败（降级 FTS）: {exc}", "DEBUG", tag=_LOG_TAG)
            items = await store.search_segments(
                q, query_vec=query_vec, speaker_id=speaker_id,
                from_ns=from_ns, to_ns=to_ns, limit=min(limit, 50))
            return {"items": items, "total": len(items)}
        return await store.list_segments(
            speaker_id=speaker_id, recording_path=recording_path,
            from_ns=from_ns, to_ns=to_ns,
            unread_only=unread_only, limit=min(limit, 200), offset=offset,
            order=order)

    async def get_segment(self, segment_id: int) -> Optional[Dict[str, Any]]:
        return await get_audio_store().get_segment(segment_id)

    async def update_segment(self, segment_id: int, req: Any) -> Optional[Dict[str, Any]]:
        store = get_audio_store()
        fields_set = req.model_fields_set
        if "transcript" in fields_set and req.transcript is not None:
            await store.update_transcript(segment_id, req.transcript)
        if "speaker_id" in fields_set:
            return await store.update_segment_speaker(segment_id, req.speaker_id)
        return await store.get_segment(segment_id)

    async def replace_transcripts(self, req: Any) -> Dict[str, Any]:
        from agent.audio.store import parse_time_ns
        return await get_audio_store().replace_in_transcripts(
            req.find, req.replace,
            speaker_id=req.speaker_id,
            from_ns=parse_time_ns(req.time_from),
            to_ns=parse_time_ns(req.time_to),
            limit=req.limit, dry_run=req.dry_run)

    async def merge_segments(self, req: Any) -> Optional[Dict[str, Any]]:
        return await get_audio_store().merge_segments(
            req.ids, transcript=req.transcript, speaker_id=req.speaker_id)

    async def split_segment(self, segment_id: int, req: Any) -> Optional[Dict[str, Any]]:
        fields_set = req.model_fields_set
        return await get_audio_store().split_segment(
            segment_id, req.at_ms,
            text_first=req.text_first, text_second=req.text_second,
            speaker_second_id=req.speaker_second_id,
            speaker_second_set="speaker_second_id" in fields_set)

    async def add_segment(self, req: Any) -> Dict[str, Any]:
        import time as _time
        store = get_audio_store()
        ts_ns = req.ts * 1_000_000_000 if req.ts else 0
        if req.recording_path:
            recording = await store.get_recording(req.recording_path)
            if not recording:
                raise FileNotFoundError("录制单元不存在")
            if not ts_ns:
                ts_ns = int(recording["started_ns"]) + req.start_ms * 1_000_000
        if not ts_ns:
            ts_ns = _time.time_ns()
        segment_id = await store.add_segment(
            recording_path=req.recording_path,
            source_file="manual", device_source="web",
            start_ms=req.start_ms, end_ms=max(req.end_ms, req.start_ms),
            part_start_ms=req.part_start_ms,
            speaker_id=req.speaker_id, transcript=req.text.strip(), ts_ns=ts_ns)
        return {"segment": await store.get_segment(segment_id)}

    async def delete_segment(self, segment_id: int) -> bool:
        return await get_audio_store().delete_segment(segment_id)

    async def mark_read(self, segment_ids: Optional[List[int]]) -> int:
        return await get_audio_store().mark_read(segment_ids)

    async def listen_segment(self, segment_id: int, apply: bool) -> Dict[str, Any]:
        from agent.audio.listen import listen_segment
        return await listen_segment(get_audio_store(), segment_id, apply=apply)

    # ------------------------------------------------------------------
    # 录制单元与统计
    # ------------------------------------------------------------------

    async def list_recordings(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        return await get_audio_store().list_recordings(limit=min(limit, 200), offset=offset)

    async def get_recording(self, path: str) -> Optional[Dict[str, Any]]:
        return await get_audio_store().get_recording(path)

    async def delete_recording(self, path: str) -> Dict[str, Any]:
        return await get_audio_store().delete_recording(path)

    async def stats(self) -> Dict[str, Any]:
        store = get_audio_store()
        result = await store.stats()
        result["match_threshold"] = matcher.global_threshold()
        result["asr_configured"] = await get_audio_registry().resolve(KIND_ASR) is not None
        result["text_embedding_model"] = str(
            ConfigManager.get("embedding_text_model", "") or "") or "default"
        return result
