"""音频能力 API 路由 — 音频页签与音频库管理的数据面（/api/audio）。

音频库本体（声纹身份/语音片段/录制单元/声纹识别）的完整管理面；
音源同步业务（目录扫描/推送接入）在实体路由 /api/entity/audiosync。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from services.audio import (
    AudioNotConfigured,
    AudioServiceFacade,
    ConfirmRequest,
    EnrollRequest,
    IdentifyCandidate,
    ImportRequest,
    ListenError,
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

router = APIRouter(prefix="/audio", tags=["audio"])

_audio = AudioServiceFacade()


# ── 状态总览 ─────────────────────────────────────────────────────


@router.get("/status")
async def status() -> Dict[str, Any]:
    """音频核心状态：提供者链可用性 + 语音会话配置 + 库统计 + 注入情况。"""
    return await _audio.status()


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    """音频库总览统计 + 配置状态。"""
    return await _audio.stats()


@router.get("/capabilities")
async def capabilities() -> Dict[str, Any]:
    """声音能力（语音合成/音色管理/音乐生成）的提供者状态与生效优先级链。"""
    return _audio.sound_capabilities()


@router.get("/funasr/status")
async def funasr_status(refresh: bool = False) -> Dict[str, Any]:
    """FunASR 转写服务状态（配置在位 + 真实可达；refresh 重置探测缓存）。"""
    return await _audio.funasr_status(refresh=refresh)


class AnalyzeRequest(BaseModel):
    path: str


@router.post("/analyze")
async def analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    """对上传目录内的音频文件执行转写并经入库管线存档（含声纹识别）。"""
    try:
        return await _audio.analyze_file(req.path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AudioNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc


# ── 声纹身份（说话人） ────────────────────────────────────────────


@router.get("/speakers")
async def list_speakers(
    status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
) -> Dict[str, Any]:
    """说话人列表（状态/关键字过滤 + 分页）。"""
    return await _audio.list_speakers(status=status, keyword=keyword,
                                      limit=limit, offset=offset)


@router.post("/speakers")
async def enroll_speaker(req: EnrollRequest) -> Dict[str, Any]:
    """注册正式说话人（向量直传）。"""
    return await _audio.enroll_speaker(req)


@router.post("/speakers/import")
async def import_speakers(req: ImportRequest) -> Dict[str, Any]:
    """冷启动批量导入：已知说话人的多条声纹样本一次建库。"""
    return await _audio.import_speakers(req)


@router.post("/speakers/merge")
async def merge_speakers(req: MergeRequest) -> Dict[str, Any]:
    """身份合并：source_id 并入 target_id。"""
    try:
        return await _audio.merge_speakers(req.source_id, req.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/speakers/prune")
async def prune_speakers(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """批量剔除待确认说话人；include_with_samples=true 时连样本一并剔除。"""
    return await _audio.prune_speakers(bool(payload.get("include_with_samples", False)))


@router.get("/speakers/similarity-map")
async def speakers_similarity_map(
    status: str = "pending", neighbors: int = 3, threshold: float = 0.0,
) -> Dict[str, Any]:
    """声纹相似度分布图：聚邻排序 + 估计人数 + 相似度矩阵（热力图用）。"""
    return await _audio.similarity_map(status, neighbors, threshold)


@router.post("/speakers/consolidate")
async def consolidate_speakers(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """相似度合并整理：质心聚类找出分裂的临时说话人 + 低价值清理。"""
    return await _audio.consolidate_speakers(payload)


@router.get("/speakers/{speaker_id}")
async def get_speaker(speaker_id: int) -> Dict[str, Any]:
    """说话人详情：档案 + 样本池 + 近期话语。"""
    detail = await _audio.speaker_detail(speaker_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return detail


@router.patch("/speakers/{speaker_id}")
async def update_speaker(speaker_id: int, req: SpeakerUpdateRequest) -> Dict[str, Any]:
    """编辑说话人档案（姓名/角色/状态/独立阈值/备注/设备来源）。"""
    updated = await _audio.update_speaker(speaker_id, **req.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return {"speaker": updated}


@router.post("/speakers/{speaker_id}/bind")
async def bind_speaker(speaker_id: int, req: SpeakerBindRequest) -> Dict[str, Any]:
    """声纹身份 ↔ 实体画像绑定（空 scope 解绑）。"""
    try:
        updated = await _audio.bind_speaker(speaker_id, req.entity_scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return {"speaker": updated}


@router.get("/speakers/by-entity/{entity_scope:path}")
async def speakers_by_entity(entity_scope: str) -> Dict[str, Any]:
    """反向查询：实体画像绑定的全部声纹身份。"""
    speakers = await _audio.speakers_for_entity(entity_scope)
    return {"entity_scope": entity_scope, "speakers": speakers}


@router.post("/speakers/{speaker_id}/confirm")
async def confirm_speaker(speaker_id: int, req: ConfirmRequest) -> Dict[str, Any]:
    """确认临时说话人：赋予正式姓名并转为已确认状态。"""
    updated = await _audio.confirm_speaker(speaker_id, req.name, role=req.role)
    if not updated:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return {"speaker": updated}


@router.delete("/speakers/{speaker_id}")
async def delete_speaker(speaker_id: int) -> Dict[str, Any]:
    """删除说话人（级联：样本池与全部话语片段一并删除）。"""
    deleted = await _audio.delete_speaker(speaker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return {"deleted": deleted}


@router.delete("/samples/{sample_id}")
async def delete_sample(sample_id: int) -> Dict[str, Any]:
    """删除单条声纹样本（样本池清理）。"""
    if not await _audio.delete_sample(sample_id):
        raise HTTPException(status_code=404, detail="样本不存在")
    return {"deleted": sample_id}


# ── 声纹识别 ──────────────────────────────────────────────────────


@router.post("/identify", response_model=List[IdentifyCandidate])
async def identify_vector(req: VectorIdentifyRequest) -> List[Dict[str, Any]]:
    """向量级识别：输入 192 维声纹向量，返回 TopK 候选及相似度。"""
    return await _audio.identify_vector(req.vector, req.top_k)


@router.post("/identify/audio")
async def identify_audio(
    file: UploadFile = File(...),
    ingest: bool = Form(default=False),
    source_time: str = Form(default=""),
) -> Dict[str, Any]:
    """音频级识别：上传音频转写+提声纹，逐段返回候选；ingest=true 时入库。

    source_time（可选）：音频原始录制时刻（epoch 毫秒或 ISO8601），透传 ASR。
    """
    try:
        return await _audio.identify_audio(
            file.filename or "", await file.read(),
            ingest=ingest, source_time=source_time)
    except AudioNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/enroll/audio")
async def enroll_audio(
    file: UploadFile = File(...),
    name: str = Form(...),
    role: str = Form(default=""),
    notes: str = Form(default=""),
) -> Dict[str, Any]:
    """音频注册：上传清晰人声音频，提声纹创建正式说话人。"""
    try:
        return await _audio.enroll_audio(
            file.filename or "", await file.read(), name, role=role, notes=notes)
    except AudioNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── 语音片段 ──────────────────────────────────────────────────────


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
    return await _audio.list_segments(
        speaker_id=speaker_id, recording_path=recording_path,
        time_from=time_from, time_to=time_to, q=q,
        unread_only=unread_only, limit=limit, offset=offset, order=order)


@router.patch("/segments/{segment_id}")
async def update_segment(segment_id: int, req: SegmentUpdateRequest) -> Dict[str, Any]:
    """编辑片段：归属改派（speaker_id，显式 null = 未知）和/或转写文本修订。"""
    if not await _audio.get_segment(segment_id):
        raise HTTPException(status_code=404, detail="片段不存在")
    fields_set = req.model_fields_set
    if "speaker_id" in fields_set and req.speaker_id is not None \
            and not await _audio.speaker_detail(req.speaker_id):
        raise HTTPException(status_code=404, detail="目标说话人不存在")
    updated = await _audio.update_segment(segment_id, req)
    return {"segment": updated}


@router.post("/segments/replace")
async def replace_transcripts(req: TranscriptReplaceRequest) -> Dict[str, Any]:
    """批量查找替换转写文本（人名/术语纠错；dry_run 预览影响面）。"""
    if req.speaker_id is not None and not await _audio.speaker_detail(req.speaker_id):
        raise HTTPException(status_code=404, detail="目标说话人不存在")
    return await _audio.replace_transcripts(req)


@router.post("/segments/merge")
async def merge_segments(req: SegmentMergeRequest) -> Dict[str, Any]:
    """合并多个相邻片段为一条（转写碎片归并，限同一录制单元内）。"""
    if req.speaker_id is not None and not await _audio.speaker_detail(req.speaker_id):
        raise HTTPException(status_code=404, detail="目标说话人不存在")
    try:
        merged = await _audio.merge_segments(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not merged:
        raise HTTPException(status_code=404, detail="片段不存在或数量不足")
    return {"segment": merged}


@router.post("/segments/listen")
async def listen_segment_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """回听片段源音源：切片重转 + 比对（apply=true 时订正文本/归属）。"""
    segment_id = int(payload.get("segment_id", 0))
    if not segment_id:
        raise HTTPException(status_code=400, detail="segment_id 必填")
    try:
        return await _audio.listen_segment(segment_id, bool(payload.get("apply", False)))
    except ListenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/segments/{segment_id}/split")
async def split_segment(segment_id: int, req: SegmentSplitRequest) -> Dict[str, Any]:
    """拆段：把片段在 at_ms 拆为两段（次段归属可指定或置未知）。"""
    fields_set = req.model_fields_set
    if "speaker_second_id" in fields_set and req.speaker_second_id is not None \
            and not await _audio.speaker_detail(req.speaker_second_id):
        raise HTTPException(status_code=404, detail="次段目标说话人不存在")
    try:
        result = await _audio.split_segment(segment_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="片段不存在")
    return result


@router.post("/segments")
async def add_segment(req: SegmentAddRequest) -> Dict[str, Any]:
    """手动新增段落（补充遗漏/记录回听内容）。"""
    if req.speaker_id is not None and not await _audio.speaker_detail(req.speaker_id):
        raise HTTPException(status_code=404, detail="目标说话人不存在")
    try:
        return await _audio.add_segment(req)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: int) -> Dict[str, Any]:
    """删除语音片段。"""
    if not await _audio.delete_segment(segment_id):
        raise HTTPException(status_code=404, detail="片段不存在")
    return {"deleted": segment_id}


@router.post("/segments/mark-read")
async def mark_read(segment_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """标记片段已读；body 为 id 数组或 null（全部）。"""
    marked = await _audio.mark_read(segment_ids)
    return {"marked_read": marked}


# ── 录制单元 ──────────────────────────────────────────────────────


@router.get("/recordings")
async def list_recordings(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """录制单元登记清单（同步的增量依据与处理结果）。"""
    return await _audio.list_recordings(limit=limit, offset=offset)


@router.delete("/recordings")
async def delete_recording(path: str = Query(...)) -> Dict[str, Any]:
    """手动删除录制单元及其衍生资源（片段/样本级联）。"""
    if not await _audio.get_recording(path):
        raise HTTPException(status_code=404, detail="录制单元不存在")
    return {"path": path, **await _audio.delete_recording(path)}
