"""入库摘要出站通知：把入库结果推送给外部系统（webhook 主动推送模式）。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import httpx

from core.config import get_config
from core.log import log
from entities._sdk import IngestPayload, IngestResult

_LOG_TAG = "音源同步"


def notify_ingested(payload: IngestPayload, result: IngestResult) -> None:
    """出站 webhook：把入库摘要推送给外部系统（未配置时静默跳过）。"""
    url = str(get_config("audiosync_outbound_webhook_url", "") or "").strip()
    if not url or not result.results:
        return
    body: Dict[str, Any] = {
        "event": "audiosync.ingested",
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
            for r in result.results
        ],
    }

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(url, json=body)
        except Exception as exc:
            log(f"出站 webhook 推送失败 [{url}]: {exc}", "WARNING", tag=_LOG_TAG)

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        log("出站 webhook 推送需要运行中的事件循环，已跳过", "DEBUG", tag=_LOG_TAG)
