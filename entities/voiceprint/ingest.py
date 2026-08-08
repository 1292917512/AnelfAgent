"""入库管线：结构化语音片段 → 声纹识别/建档 → 落库 → 向量回填 → 出站通知。

数据流（对应上游 pipeline 的一次音频处理结果）：
    IngestPayload
      → 噪音过滤（纯标点/空文本段直接跳过，不计片段不建档）
      → 短段策略（时长 < voiceprint_min_voiceprint_ms 的段声纹不可靠：
          只做转写留存（speaker=NULL），不匹配不建档；可选 attach 模式
          挂到同录制前一段的说话人）
      → 逐段 matcher.identify（已知人命中回写 + 样本累积 / 新人建临时档案）
      → store.add_segment（未读收件箱 +1）
      → wake_embedding_worker（后台回填转写文本向量）
      → 配置 outbound_webhook_url 时 fire-and-forget POST 本次入库摘要
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from typing import Any, Dict, List, Optional

import httpx

from core.config import get_config, get_config_bool, get_config_int
from core.log import log

from . import matcher
from .schemas import IngestPayload, IngestResult, IngestResultItem
from .store import VoiceprintStore, get_voiceprint_store


def _is_noise_text(text: str) -> bool:
    """纯标点/空白文本判定（无有效语义内容，不建档也不计片段）。"""
    for ch in text:
        if ch.isspace():
            continue
        # 标点/符号类别（P*/S*）之外的字符存在即视为有效内容
        if not unicodedata.category(ch).startswith(("P", "S")):
            return False
    return True


def _min_voiceprint_ms() -> int:
    """参与声纹识别/建档的最小段时长（短段声纹不可靠，只留存文本）。"""
    return max(0, get_config_int("voiceprint_min_voiceprint_ms", 2000))


def _attach_unidentified() -> bool:
    """未识别段（无声纹向量/短段）是否挂到同录制最近的已归属段（默认开）。

    开启后库里不再出现归属悬空的片段：短段/无向量段归入相邻说话人；
    仅当整段录制无任何已归属段时才保留未知。误挂风险由用户按场景权衡
    （会议连读场景通常正确）。
    """
    return get_config_bool("voiceprint_attach_unidentified", True)


async def ingest_payload(
    payload: IngestPayload,
    *,
    store: Optional[VoiceprintStore] = None,
) -> IngestResult:
    """处理一次上游推送：逐段识别并入库，返回批处理结果。"""
    store = store or get_voiceprint_store()
    base_ts_ns = int(payload.ts * 1_000_000_000) if payload.ts else time.time_ns()
    skip_noise = get_config_bool("voiceprint_skip_noise_segments", True)
    min_ms = _min_voiceprint_ms()

    results: List[IngestResultItem] = []
    skipped = 0
    for seg in payload.segments:
        # 噪音段：纯标点/空白 → 整体跳过（不建档不计片段）
        if skip_noise and _is_noise_text(seg.text):
            skipped += 1
            continue

        speaker_id: Optional[int] = None
        speaker_key = ""
        speaker_name = ""
        similarity = 0.0
        is_new = False
        sample_added = False

        audio_ms = max(0, seg.end_ms - seg.start_ms)
        short_segment = 0 < audio_ms < min_ms
        if seg.vector and not short_segment:
            identified = await matcher.identify(
                store, seg.vector, audio_ms=audio_ms, ts_ns=base_ts_ns)
            speaker = identified["speaker"]
            if speaker:
                speaker_id = int(speaker["id"])
                speaker_key = str(speaker["speaker_key"])
                speaker_name = str(speaker["name"])
            similarity = float(identified["similarity"])
            is_new = bool(identified["is_new"])
            sample_added = bool(identified.get("sample_added"))

        segment_ts_ns = (seg.abs_start_ms * 1_000_000) if seg.abs_start_ms is not None \
            else base_ts_ns + seg.start_ms * 1_000_000
        segment_id = await store.add_segment(
            recording_path=payload.recording_path,
            source_file=payload.source_file,
            device_source=payload.device_source,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            part_start_ms=seg.part_start_ms,
            speaker_id=speaker_id,
            is_new_speaker=is_new,
            similarity=similarity,
            transcript=seg.text,
            ts_ns=segment_ts_ns,
        )
        if speaker_id is not None and sample_added:
            # 样本挂接片段：录制删除时级联清理声纹样本（仅本次新增的样本，
            # 避免误挂 enroll/import 的建档样本）
            await store.attach_segment_to_latest_sample(speaker_id, segment_id)
        results.append(IngestResultItem(
            segment_id=segment_id,
            speaker_id=speaker_id,
            speaker_key=speaker_key,
            speaker_name=speaker_name,
            similarity=similarity,
            is_new_speaker=is_new,
        ))

    # 未识别段挂接：短段/无声纹段归入同录制最近的已归属段（防归属悬空）
    if _attach_unidentified() and any(r.speaker_id is None for r in results):
        attributed = [r.speaker_id for r in results if r.speaker_id is not None]
        if attributed:
            last_known: Optional[int] = None
            for idx, item in enumerate(results):
                if item.speaker_id is not None:
                    last_known = item.speaker_id
                    continue
                target = last_known
                if target is None:
                    # 前面没有已归属段：找后面最近的
                    for later in results[idx + 1:]:
                        if later.speaker_id is not None:
                            target = later.speaker_id
                            break
                if target is not None:
                    await store.update_segment_speaker(item.segment_id, target)
                    item.speaker_id = target
                    last_known = target

    if results:
        try:
            from agent.memory.embedding import wake_embedding_worker
            wake_embedding_worker()
        except Exception as exc:
            log(f"embedding worker 唤醒失败: {exc}", "DEBUG", tag="音源库")
        _notify_outbound(payload, results)

    return IngestResult(ingested=len(results), skipped=skipped, results=results)


def _notify_outbound(payload: IngestPayload, results: List[IngestResultItem]) -> None:
    """出站 webhook：把入库摘要推送给外部系统（Agent 对接的主动推送模式）。"""
    url = str(get_config("voiceprint_outbound_webhook_url", "") or "").strip()
    if not url:
        return
    body: Dict[str, Any] = {
        "event": "voiceprint.ingested",
        "source_file": payload.source_file,
        "recording_path": payload.recording_path,
        "device_source": payload.device_source,
        "segments": [
            {
                "segment_id": r.segment_id,
                "speaker_id": r.speaker_id,
                "speaker_key": r.speaker_key,
                "speaker_name": r.speaker_name,
                "similarity": r.similarity,
                "is_new_speaker": r.is_new_speaker,
            }
            for r in results
        ],
    }

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(url, json=body)
        except Exception as exc:
            log(f"出站 webhook 推送失败 [{url}]: {exc}", "WARNING", tag="音源库")

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        log("出站 webhook 推送需要运行中的事件循环，已跳过", "DEBUG", tag="音源库")
