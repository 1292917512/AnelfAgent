"""音源库实体的 HTTP 路由（自动挂载到 /api/entity/voiceprint）。

经 web/server.py 的 _mount_entity_routers 扫描发现：
- /ingest：上游 pipeline 推送（X-Ingest-Token 鉴权，路径在 _AUTH_EXEMPT 白名单，
  由本端点自行校验令牌；未配置 voiceprint_ingest_token 时 fail-closed）
- /speakers/*：说话人档案 CRUD / 确认 / 合并 / 注册 / 批量导入
- /identify*：声纹识别（向量直传 / 音频上传经 FunASR）
- /segments/*：语音片段查询 / 归属改派 / 已读
- /stats：库总览
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile

from core.config import ConfigManager, ConfigRegistry, get_config
from core.log import log

from . import client, matcher
from .ingest import ingest_payload
from .schemas import (
    ConfirmRequest,
    EnrollRequest,
    IdentifyCandidate,
    ImportRequest,
    IngestPayload,
    IngestResult,
    MergeRequest,
    SegmentAddRequest,
    SegmentMergeRequest,
    SegmentSplitRequest,
    SegmentUpdateRequest,
    SpeakerUpdateRequest,
    TranscriptReplaceRequest,
    VectorIdentifyRequest,
)
from .store import get_voiceprint_store, parse_time_ns


def _verify_ingest_token(request: Request) -> None:
    """校验上游推送令牌；未配置令牌时 ingest 关闭（fail-closed）。"""
    token = str(get_config("voiceprint_ingest_token", "") or "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="ingest 未启用：请在实体配置中设置 voiceprint_ingest_token")
    provided = request.headers.get("x-ingest-token", "")
    if provided != token:
        raise HTTPException(status_code=401, detail="ingest token 无效")


async def _transcribe_upload(upload: UploadFile, source_time: str = "") -> List[Dict[str, Any]]:
    """把上传音频落地临时文件并调用 FunASR 转写。"""
    if not client.is_configured():
        raise HTTPException(
            status_code=503,
            detail="未配置 FunASR 服务（voiceprint_funasr_endpoint）")
    suffix = os.path.splitext(upload.filename or "audio.wav")[1] or ".wav"
    fd, tmp_path = tempfile.mkstemp(prefix="voiceprint_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(await upload.read())
        return await client.transcribe(tmp_path, source_time=source_time)
    except client.FunAsrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_router() -> APIRouter:
    router = APIRouter()

    # ── 实体配置（框架 /api/entities/{name}/config 对纯分组实体 404，
    #    面板走实体自有配置端点，与 media 实体同一模式）────────────────

    @router.get("/config")
    async def get_entity_config() -> Dict[str, Any]:
        """读取 voiceprint 分组的配置项与当前值。"""
        items = []
        for item in ConfigRegistry.get_group_items("voiceprint"):
            items.append({
                "key": item.key,
                "description": item.description,
                "value_type": item.value_type.value
                if hasattr(item.value_type, "value") else str(item.value_type),
                "default_value": item.default_value,
                "current_value": ConfigManager.get(item.key, item.default_value),
            })
        return {"items": items}

    @router.put("/config")
    async def update_entity_config(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """批量更新配置（仅接受 voiceprint 分组内已注册的键）。"""
        updates = payload.get("updates")
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates 必须是对象")
        valid_keys = {item.key for item in ConfigRegistry.get_group_items("voiceprint")}
        count = 0
        for key, value in updates.items():
            if key not in valid_keys:
                continue
            ConfigManager.set(key, value)
            count += 1
        if count:
            ConfigManager.save()
            log(f"音源库配置已更新: {sorted(k for k in updates if k in valid_keys)}", tag="音源库")
        return {"updated": count}

    # ── 上游 pipeline 推送 ─────────────────────────────────────────

    @router.post("/ingest", response_model=IngestResult)
    async def ingest(request: Request, payload: IngestPayload) -> IngestResult:
        """接收上游 pipeline（文件监听 + FunASR）推送的结构化语音片段。"""
        _verify_ingest_token(request)
        result = await ingest_payload(payload)
        log(f"ingest 入库 {result.ingested} 段 [{payload.source_file}]", tag="音源库")
        return result

    # ── 说话人档案 ────────────────────────────────────────────────

    @router.get("/speakers")
    async def list_speakers(
        status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        """说话人列表（状态/关键字过滤 + 分页）。"""
        store = get_voiceprint_store()
        return await store.list_speakers(
            status=status, keyword=keyword, limit=min(limit, 200), offset=offset)

    @router.post("/speakers")
    async def enroll_speaker(req: EnrollRequest) -> Dict[str, Any]:
        """注册正式说话人（向量直传）。"""
        store = get_voiceprint_store()
        return await matcher.enroll(
            store, req.name, req.vector, role=req.role,
            notes=req.notes, device_source=req.device_source)

    @router.post("/speakers/import")
    async def import_speakers(req: ImportRequest) -> Dict[str, Any]:
        """冷启动批量导入：已知说话人的多条声纹样本一次建库。"""
        store = get_voiceprint_store()
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

    @router.post("/speakers/merge")
    async def merge_speakers(req: MergeRequest) -> Dict[str, Any]:
        """身份合并：source_id 并入 target_id。"""
        store = get_voiceprint_store()
        try:
            return await matcher.merge(store, req.source_id, req.target_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/speakers/prune")
    async def prune_speakers(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """批量剔除待确认说话人；include_with_samples=true 时连样本一并剔除。"""
        store = get_voiceprint_store()
        include = bool(payload.get("include_with_samples", False))
        deleted = await store.prune_pending_speakers(include_with_samples=include)
        return {"pruned": len(deleted), "include_with_samples": include,
                "speakers": deleted}

    @router.get("/speakers/similarity-map")
    async def speakers_similarity_map(
        status: str = "pending", neighbors: int = 3, threshold: float = 0.0,
    ) -> Dict[str, Any]:
        """声纹相似度分布图：聚邻排序 + 估计人数 + 相似度矩阵（热力图用）。"""
        from .consolidate import similarity_map
        store = get_voiceprint_store()
        return await similarity_map(
            store, status=status, neighbors=neighbors,
            threshold=threshold if 0.0 < threshold < 1.0 else None)

    @router.post("/speakers/consolidate")
    async def consolidate_speakers(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """相似度合并整理：质心聚类找出分裂的临时说话人 + 低价值清理。"""
        from .consolidate import consolidate
        store = get_voiceprint_store()
        threshold = payload.get("threshold")
        return await consolidate(
            store,
            threshold=float(threshold) if isinstance(threshold, (int, float))
                      and 0.0 < float(threshold) < 1.0 else None,
            dry_run=bool(payload.get("dry_run", True)),
            status="" if payload.get("include_confirmed") else "pending",
            prune_insignificant=bool(payload.get("prune_insignificant", False)),
        )

    @router.get("/speakers/{speaker_id}")
    async def get_speaker(speaker_id: int) -> Dict[str, Any]:
        """说话人详情：档案 + 样本池 + 近期话语。"""
        store = get_voiceprint_store()
        speaker = await store.get_speaker(speaker_id)
        if not speaker:
            raise HTTPException(status_code=404, detail="说话人不存在")
        samples = await store.list_samples(speaker_id)
        recent = await store.list_segments(speaker_id=speaker_id, limit=5)
        return {
            "speaker": speaker,
            "effective_threshold": matcher.effective_threshold(speaker),
            "samples": samples,
            "recent_segments": recent["items"],
        }

    @router.patch("/speakers/{speaker_id}")
    async def update_speaker(speaker_id: int, req: SpeakerUpdateRequest) -> Dict[str, Any]:
        """编辑说话人档案（姓名/角色/状态/独立阈值/备注/设备来源）。"""
        store = get_voiceprint_store()
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        updated = await store.update_speaker(speaker_id, **fields)
        if not updated:
            raise HTTPException(status_code=404, detail="说话人不存在")
        return {"speaker": updated}

    @router.post("/speakers/{speaker_id}/confirm")
    async def confirm_speaker(speaker_id: int, req: ConfirmRequest) -> Dict[str, Any]:
        """确认临时说话人：赋予正式姓名并转为已确认状态。"""
        store = get_voiceprint_store()
        updated = await matcher.confirm(store, speaker_id, req.name, role=req.role)
        if not updated:
            raise HTTPException(status_code=404, detail="说话人不存在")
        return {"speaker": updated}

    @router.delete("/speakers/{speaker_id}")
    async def delete_speaker(speaker_id: int) -> Dict[str, Any]:
        """删除说话人（样本池一并删除，片段标记为未知）。"""
        store = get_voiceprint_store()
        deleted = await store.delete_speaker(speaker_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="说话人不存在")
        return {"deleted": deleted}

    @router.delete("/samples/{sample_id}")
    async def delete_sample(sample_id: int) -> Dict[str, Any]:
        """删除单条声纹样本（样本池清理）。"""
        store = get_voiceprint_store()
        if not await store.delete_sample(sample_id):
            raise HTTPException(status_code=404, detail="样本不存在")
        return {"deleted": sample_id}

    # ── 声纹识别 ──────────────────────────────────────────────────

    @router.post("/identify", response_model=List[IdentifyCandidate])
    async def identify_vector(req: VectorIdentifyRequest) -> List[IdentifyCandidate]:
        """向量级识别：输入 192 维声纹向量，返回 TopK 候选及相似度。"""
        store = get_voiceprint_store()
        candidates = await matcher.match_vector(store, req.vector, top_k=req.top_k)
        return [IdentifyCandidate(**c) for c in candidates]

    @router.post("/identify/audio")
    async def identify_audio(
        file: UploadFile = File(...),
        ingest: bool = Form(default=False),
        source_time: str = Form(default=""),
    ) -> Dict[str, Any]:
        """音频级识别：上传音频经 FunASR 转写+提声纹，逐段返回候选；ingest=true 时入库。

        source_time（可选）：音频原始录制时刻（epoch 毫秒或 ISO8601），透传 FunASR。
        """
        segments = await _transcribe_upload(file, source_time=source_time)
        if ingest:
            from .schemas import SegmentIn
            result = await ingest_payload(IngestPayload(
                source_file=file.filename or "",
                segments=[SegmentIn(
                    start_ms=s["start_ms"], end_ms=s["end_ms"],
                    text=s["text"], vector=s["vector"],
                    abs_start_ms=s.get("abs_start_ms"),
                    abs_end_ms=s.get("abs_end_ms"),
                ) for s in segments],
            ))
            return {"ingested": True, **result.model_dump()}
        store = get_voiceprint_store()
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

    @router.post("/enroll/audio")
    async def enroll_audio(
        file: UploadFile = File(...),
        name: str = Form(...),
        role: str = Form(default=""),
        notes: str = Form(default=""),
    ) -> Dict[str, Any]:
        """音频注册：上传清晰人声音频，经 FunASR 提声纹创建正式说话人。"""
        segments = await _transcribe_upload(file)
        vectors = [s["vector"] for s in segments if s.get("vector")]
        if not vectors:
            raise HTTPException(status_code=422, detail="音频中未提取到有效声纹")
        store = get_voiceprint_store()
        speaker = await matcher.enroll(
            store, name, vectors[0], role=role, notes=notes,
            device_source=file.filename or "")
        for vec in vectors[1:matcher.max_samples_per_speaker()]:
            await store.add_sample(int(speaker["id"]), vec, source="enroll",
                                   max_samples=matcher.max_samples_per_speaker())
        return {"speaker": speaker, "samples_enrolled": len(vectors)}

    # ── 语音片段 ──────────────────────────────────────────────────

    @router.get("/segments")
    async def list_segments(
        speaker_id: Optional[int] = None,
        recording_path: str = "",
        time_from: str = "", time_to: str = "",
        q: str = "",
        unread_only: bool = False,
        limit: int = 20, offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """片段查询：q 非空走语义+全文混合检索，否则时间线（说话人/录制/时间/未读过滤）。

        order=asc 时按时间正序返回（时间线视图）。"""
        store = get_voiceprint_store()
        from_ns = parse_time_ns(time_from)
        to_ns = parse_time_ns(time_to)
        if q.strip():
            query_vec = None
            try:
                from agent.memory.embedding import get_embedder
                query_vec = await get_embedder("text").embed_query(q)
            except Exception as exc:
                log(f"查询向量化失败（降级 FTS）: {exc}", "DEBUG", tag="音源库")
            items = await store.search_segments(
                q, query_vec=query_vec, speaker_id=speaker_id,
                from_ns=from_ns, to_ns=to_ns, limit=min(limit, 50))
            return {"items": items, "total": len(items)}
        return await store.list_segments(
            speaker_id=speaker_id, recording_path=recording_path,
            from_ns=from_ns, to_ns=to_ns,
            unread_only=unread_only, limit=min(limit, 200), offset=offset,
            order=order)

    @router.patch("/segments/{segment_id}")
    async def update_segment(segment_id: int, req: SegmentUpdateRequest) -> Dict[str, Any]:
        """编辑片段：归属改派（speaker_id，显式 null = 未知）和/或转写文本修订。"""
        store = get_voiceprint_store()
        if not await store.get_segment(segment_id):
            raise HTTPException(status_code=404, detail="片段不存在")
        fields_set = req.model_fields_set
        if "speaker_id" in fields_set and req.speaker_id is not None \
                and not await store.get_speaker(req.speaker_id):
            raise HTTPException(status_code=404, detail="目标说话人不存在")
        if "transcript" in fields_set and req.transcript is not None:
            await store.update_transcript(segment_id, req.transcript)
        if "speaker_id" in fields_set:
            updated = await store.update_segment_speaker(segment_id, req.speaker_id)
        else:
            updated = await store.get_segment(segment_id)
        return {"segment": updated}

    @router.post("/segments/replace")
    async def replace_transcripts(req: TranscriptReplaceRequest) -> Dict[str, Any]:
        """批量查找替换转写文本（人名/术语纠错；dry_run 预览影响面）。"""
        store = get_voiceprint_store()
        if req.speaker_id is not None and not await store.get_speaker(req.speaker_id):
            raise HTTPException(status_code=404, detail="目标说话人不存在")
        return await store.replace_in_transcripts(
            req.find, req.replace,
            speaker_id=req.speaker_id,
            from_ns=parse_time_ns(req.time_from),
            to_ns=parse_time_ns(req.time_to),
            limit=req.limit, dry_run=req.dry_run)

    @router.post("/segments/merge")
    async def merge_segments(req: SegmentMergeRequest) -> Dict[str, Any]:
        """合并多个相邻片段为一条（转写碎片归并，限同一录制单元内）。"""
        store = get_voiceprint_store()
        if req.speaker_id is not None and not await store.get_speaker(req.speaker_id):
            raise HTTPException(status_code=404, detail="目标说话人不存在")
        try:
            merged = await store.merge_segments(
                req.ids, transcript=req.transcript, speaker_id=req.speaker_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not merged:
            raise HTTPException(status_code=404, detail="片段不存在或数量不足")
        return {"segment": merged}

    @router.post("/segments/listen")
    async def listen_segment_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """回听片段源音源：切片重转 + 比对（apply=true 时订正文本/归属）。"""
        from .listen import ListenError, listen_segment
        segment_id = int(payload.get("segment_id", 0))
        if not segment_id:
            raise HTTPException(status_code=400, detail="segment_id 必填")
        store = get_voiceprint_store()
        try:
            return await listen_segment(
                store, segment_id, apply=bool(payload.get("apply", False)))
        except ListenError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/segments/{segment_id}/split")
    async def split_segment(segment_id: int, req: SegmentSplitRequest) -> Dict[str, Any]:
        """拆段：把片段在 at_ms 拆为两段（次段归属可指定或置未知）。"""
        store = get_voiceprint_store()
        fields_set = req.model_fields_set
        if "speaker_second_id" in fields_set and req.speaker_second_id is not None \
                and not await store.get_speaker(req.speaker_second_id):
            raise HTTPException(status_code=404, detail="次段目标说话人不存在")
        try:
            result = await store.split_segment(
                segment_id, req.at_ms,
                text_first=req.text_first, text_second=req.text_second,
                speaker_second_id=req.speaker_second_id,
                speaker_second_set="speaker_second_id" in fields_set)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result:
            raise HTTPException(status_code=404, detail="片段不存在")
        return result

    @router.post("/segments")
    async def add_segment(req: SegmentAddRequest) -> Dict[str, Any]:
        """手动新增段落（补充遗漏/记录回听内容）。"""
        store = get_voiceprint_store()
        if req.speaker_id is not None and not await store.get_speaker(req.speaker_id):
            raise HTTPException(status_code=404, detail="目标说话人不存在")
        import time as _time
        ts_ns = req.ts * 1_000_000_000 if req.ts else 0
        if req.recording_path:
            recording = await store.get_recording(req.recording_path)
            if not recording:
                raise HTTPException(status_code=404, detail="录制单元不存在")
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
        segment = await store.get_segment(segment_id)
        return {"segment": segment}

    @router.delete("/segments/{segment_id}")
    async def delete_segment(segment_id: int) -> Dict[str, Any]:
        """删除语音片段。"""
        store = get_voiceprint_store()
        if not await store.delete_segment(segment_id):
            raise HTTPException(status_code=404, detail="片段不存在")
        return {"deleted": segment_id}

    @router.post("/segments/mark-read")
    async def mark_read(segment_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """标记片段已读；body 为 id 数组或 null（全部）。"""
        store = get_voiceprint_store()
        marked = await store.mark_read(segment_ids)
        return {"marked_read": marked}

    # ── 目录自动同步 ──────────────────────────────────────────────

    @router.post("/sync")
    async def sync_now() -> Dict[str, Any]:
        """手动触发一轮目录增量同步。"""
        from .watcher import get_voiceprint_watcher
        watcher = get_voiceprint_watcher()
        result = await watcher.sync_now()
        result["status"] = watcher.status()
        return result

    @router.get("/sync/status")
    async def sync_status() -> Dict[str, Any]:
        """目录同步状态（来源/最近扫描/最近结果/错误）。"""
        from .watcher import get_voiceprint_watcher
        return get_voiceprint_watcher().status()

    @router.get("/sync/preview")
    async def sync_preview() -> Dict[str, Any]:
        """待同步预览：NAS 与登记表 diff（只读不处理），含待同步单元清单。"""
        from .watcher import get_voiceprint_watcher
        return await get_voiceprint_watcher().preview()

    # ── OpenList 状态 ─────────────────────────────────────────────

    @router.get("/openlist/status")
    async def openlist_status() -> Dict[str, Any]:
        """OpenList 连通性体检（配置/可达/延迟）。"""
        from .openlist import check_status
        return await check_status()

    @router.get("/sync/files")
    async def sync_files(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """录制单元登记清单（镜像同步的增量依据与处理结果）。"""
        store = get_voiceprint_store()
        return await store.list_recordings(limit=min(limit, 200), offset=offset)

    @router.delete("/sync/files")
    async def delete_recording(path: str) -> Dict[str, Any]:
        """手动删除录制单元及其衍生资源（片段/样本级联）。"""
        store = get_voiceprint_store()
        if not await store.get_recording(path):
            raise HTTPException(status_code=404, detail="录制单元不存在")
        return {"path": path, **await store.delete_recording(path)}

    @router.post("/sync/rebuild")
    async def rebuild_recordings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """删除重建：指定录制单元（paths 数组）清理本地资源并立即重新入库。"""
        paths = payload.get("paths")
        if not isinstance(paths, list) or not paths:
            raise HTTPException(status_code=400, detail="paths 须为非空数组")
        from .watcher import get_voiceprint_watcher
        return await get_voiceprint_watcher().rebuild([str(p) for p in paths[:50]])

    # ── 统计 ──────────────────────────────────────────────────────

    @router.get("/stats")
    async def stats() -> Dict[str, Any]:
        """库总览统计 + 配置状态。"""
        store = get_voiceprint_store()
        result = await store.stats()
        result["match_threshold"] = matcher.global_threshold()
        result["funasr_configured"] = client.is_configured()
        result["ingest_enabled"] = bool(
            str(get_config("voiceprint_ingest_token", "") or "").strip())
        result["text_embedding_model"] = str(
            get_config("embedding_text_model", "") or "") or "default"
        from .watcher import get_voiceprint_watcher
        result["watch"] = get_voiceprint_watcher().status()
        return result

    return router
