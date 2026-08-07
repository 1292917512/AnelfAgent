"""源音源回听订正：定位片段对应的原始音频 → 切片 → 重新转写 → 订正。

定位原理（与同步管线完全对齐）：
- recordings.files_json 保存合并清单 [{path, duration_s}]（按合并顺序）
- 片段在整体合并音频上的区间 = part_start_ms + [start_ms, end_ms]
- 沿清单累计时长找到覆盖该区间的源文件（OpenList 重新下载 / 本地直读），
  单文件直切、跨文件先并后切，得到与入库时完全一致的音频切片
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Tuple

from core.config import ConfigManager
from core.log import log

from . import client, ffmpeg, matcher, openlist
from .store import VoiceprintStore


class ListenError(RuntimeError):
    """回听失败（清单缺失/源文件不可达/转写失败）。"""


def _clips_dir() -> str:
    try:
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    path = os.path.join(os.path.abspath(ws), "voiceprint_clips")
    os.makedirs(path, exist_ok=True)
    return path


def _merged_range(segment: Dict[str, Any]) -> Tuple[float, float]:
    """片段在整体合并音频上的时间区间（秒）。"""
    offset_ms = int(segment.get("part_start_ms") or 0)
    return ((offset_ms + int(segment["start_ms"])) / 1000.0,
            (offset_ms + int(segment["end_ms"])) / 1000.0)


def _overlapping_files(
    manifest: List[Dict[str, Any]], start_s: float, end_s: float,
) -> List[Dict[str, Any]]:
    """找出覆盖 [start_s, end_s) 的源文件（带其合并内偏移 file_offset_s）。"""
    result: List[Dict[str, Any]] = []
    cursor = 0.0
    for item in manifest:
        duration = float(item.get("duration_s") or 0.0)
        if duration > 0 and cursor < end_s and cursor + duration > start_s:
            result.append({**item, "file_offset_s": cursor})
        cursor += duration
    return result


async def _fetch_source(path: str) -> Tuple[str, bool]:
    """取回源文件本地路径。返回 (local_path, 是否需要清理)。"""
    if openlist.is_configured():
        return await openlist.download(path), True
    if not os.path.isfile(path):
        raise ListenError(f"源文件不存在: {path}")
    return path, False


async def build_clip(
    segment: Dict[str, Any], manifest: List[Dict[str, Any]],
) -> Tuple[str, List[str]]:
    """构建片段音频切片。返回 (clip_wav_path, 待清理临时文件列表)。

    Raises:
        ListenError: 清单为空 / 区间无覆盖文件 / 源文件不可达。
    """
    if not manifest:
        raise ListenError("该录制缺少合并清单（files_json 为空，旧数据），"
                          "请对该录制执行一次删除重建后再回听")
    start_s, end_s = _merged_range(segment)
    overlapping = _overlapping_files(manifest, start_s, end_s)
    if not overlapping:
        raise ListenError(
            f"合并清单中没有覆盖 {start_s:.1f}~{end_s:.1f}s 的源文件（清单与实际可能已不一致）")

    temps: List[str] = []
    try:
        fetched: List[Dict[str, Any]] = []
        for item in overlapping:
            local, is_temp = await _fetch_source(item["path"])
            if is_temp:
                temps.append(local)
            fetched.append({**item, "local": local})

        first = fetched[0]
        clip_start = start_s - float(first["file_offset_s"])
        clip_end = end_s - float(first["file_offset_s"])
        fd, clip_path = tempfile.mkstemp(prefix="voiceprint_clip_", suffix=".wav")
        os.close(fd)
        temps.append(clip_path)

        if len(fetched) == 1:
            # 单文件覆盖：直接切（先统一转 16k 单声道保证 -c copy 可切）
            wav_path, converted = await ffmpeg.ensure_16k_mono_wav(first["local"])
            if converted:
                temps.append(wav_path)
            await ffmpeg.split_wav(wav_path, max(0.0, clip_start),
                                   max(clip_start + 0.05, clip_end), clip_path)
        else:
            # 跨文件：先按序并接再切
            merged = await ffmpeg.merge_to_wav([f["local"] for f in fetched])
            temps.append(merged)
            await ffmpeg.split_wav(merged, max(0.0, clip_start),
                                   max(clip_start + 0.05, clip_end), clip_path)
        return clip_path, temps
    except Exception:
        for tmp in temps:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


async def listen_segment(
    store: VoiceprintStore,
    segment_id: int,
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """回听片段源音源：切片 → 重新转写 → 比对（可选订正文本与归属）。

    Returns:
        {
            "segment_id", "clip_path", "duration_s",
            "current_text", "fresh_text", "changed",
            "speaker_now": {...}|None, "speaker_fresh": {...}|None,
            "applied": bool,
        }
    """
    if not client.is_configured():
        raise ListenError("未配置 FunASR 服务（voiceprint_funasr_endpoint）")
    segment = await store.get_segment(segment_id)
    if not segment:
        raise ListenError(f"片段不存在: {segment_id}")
    recording_path = str(segment.get("recording_path") or "")
    if not recording_path:
        raise ListenError("该片段无录制单元归属（非目录同步入库），无法回听")
    recording = await store.get_recording(recording_path)
    if not recording:
        raise ListenError(f"录制单元已不存在: {recording_path}")

    clip_path, temps = await build_clip(segment, recording["files"])
    try:
        source_time = str(int(segment["ts_ns"]) // 1_000_000)
        fresh_segments = await client.transcribe(clip_path, source_time=source_time)
    finally:
        for tmp in temps:
            if tmp != clip_path:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    fresh_text = " ".join(s["text"].strip() for s in fresh_segments if s["text"].strip())
    fresh_vector = next((s["vector"] for s in fresh_segments if s.get("vector")), None)

    # 切片留存到 workspace（AI 可用其他多媒体工具继续处理）
    keep_path = os.path.join(_clips_dir(), f"clip_{segment_id}.wav")
    try:
        import shutil
        shutil.move(clip_path, keep_path)
    except OSError:
        keep_path = clip_path

    result: Dict[str, Any] = {
        "segment_id": segment_id,
        "clip_path": keep_path,
        "duration_s": round((int(segment["end_ms"]) - int(segment["start_ms"])) / 1000, 2),
        "current_text": segment["transcript"],
        "fresh_text": fresh_text,
        "changed": fresh_text.strip() != segment["transcript"].strip(),
        "speaker_now": None,
        "speaker_fresh": None,
        "applied": False,
    }
    if segment["speaker_id"]:
        result["speaker_now"] = await store.get_speaker(int(segment["speaker_id"]))
    if fresh_vector:
        candidates = await matcher.match_vector(store, fresh_vector, top_k=3)
        result["speaker_fresh"] = candidates[0] if candidates else None
        result["candidates"] = candidates

    if apply and (fresh_text or fresh_vector):
        if fresh_text and fresh_text.strip() != segment["transcript"].strip():
            await store.update_transcript(segment_id, fresh_text)
            result["applied"] = True
        fresh_speaker = result.get("speaker_fresh")
        if fresh_speaker and fresh_speaker.get("matched") \
                and int(fresh_speaker["id"]) != (segment["speaker_id"] or 0):
            await store.update_segment_speaker(segment_id, int(fresh_speaker["id"]))
            result["applied"] = True
        if result["applied"]:
            log(f"回听订正片段 {segment_id}: {fresh_text[:40]}", tag="音源库")
            result["segment"] = await store.get_segment(segment_id)
    return result
