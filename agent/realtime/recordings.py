"""实时通话录音留存：轮次 PCM 落盘 + 到期滚动清理。

留存使实时轮次在音源库中可回听/订正/重转写（入库载荷携带 recording_path）。
文件按日分目录存放 workspace/uploads/realtime/<YYYYMMDD>/；每次留存顺带按
realtime_recording_retention_days 惰性清理过期日目录（目录名即日期，
列举开销极小，无需独立调度）。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import wave

from core.config import get_config_bool, get_config_int
from core.log import log

_LOG_TAG = "实时语音"


def _recordings_dir(day: str = "") -> str:
    from core.path import workspace_root
    base = os.path.join(workspace_root(), "uploads", "realtime")
    return os.path.join(base, day) if day else base


async def save_turn_recording(owner: str, turn_id: int, pcm: bytes, sample_rate: int) -> str:
    """轮次 PCM 留存为 WAV；开关关闭/音频为空/写失败均返回空串（不阻塞收束）。"""
    if not pcm or not get_config_bool("realtime_keep_recordings", True):
        return ""
    day = time.strftime("%Y%m%d")
    safe_owner = re.sub(r"[^0-9A-Za-z_\-]", "_", owner)[:40] or "anon"
    name = f"{safe_owner}_t{turn_id}_{int(time.time())}.wav"

    def _write() -> str:
        dir_path = _recordings_dir(day)
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, name)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return path

    try:
        path = await asyncio.to_thread(_write)
    except Exception as exc:
        log(f"轮次录音留存失败（跳过）: {exc}", "DEBUG", tag=_LOG_TAG)
        return ""
    await sweep_expired()
    return path


async def sweep_expired() -> None:
    """清理超过保留天数的日目录（含其中全部轮次录音）。"""
    days = get_config_int("realtime_recording_retention_days", 7)
    if days <= 0:
        return

    def _sweep() -> None:
        import shutil

        base = _recordings_dir()
        try:
            entries = os.listdir(base)
        except OSError:
            return
        cutoff = time.time() - days * 86400
        for entry in entries:
            if not re.fullmatch(r"\d{8}", entry):
                continue
            try:
                day_ts = time.mktime(time.strptime(entry, "%Y%m%d"))
            except (ValueError, OverflowError):
                continue
            if day_ts < cutoff:
                shutil.rmtree(os.path.join(base, entry), ignore_errors=True)

    try:
        await asyncio.to_thread(_sweep)
    except Exception as exc:
        log(f"录音留存清理失败（跳过）: {exc}", "DEBUG", tag=_LOG_TAG)
