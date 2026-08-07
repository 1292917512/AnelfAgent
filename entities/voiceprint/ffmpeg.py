"""音频预处理：统一转为 16kHz 单声道 WAV（FunASR 最佳输入）。

策略：
- ffprobe 探测源格式；已是 16k 单声道 PCM WAV 时直接复用源文件（零转换）
- 视频容器 / AMR / WMA / 非常规采样率等一律经 ffmpeg 转码
- 无人声过滤不在此层做：FunASR 的 fsmn-vad 会自动丢弃非语音段，
  转写结果为空时由 watcher 标记 no_speech
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from typing import Any, Dict, Optional

from core.config import get_config
from core.log import log


class PreprocessError(RuntimeError):
    """ffmpeg/ffprobe 不可用或转码失败。"""


def _ffmpeg_bin() -> str:
    return str(get_config("voiceprint_ffmpeg_bin", "ffmpeg") or "ffmpeg").strip() or "ffmpeg"


def _ffprobe_bin() -> str:
    configured = str(get_config("voiceprint_ffmpeg_bin", "ffmpeg") or "").strip()
    # 同目录下的 ffprobe（ffmpeg 与 ffprobe 通常成对安装）
    if configured and os.sep in configured:
        candidate = os.path.join(os.path.dirname(configured), "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    return "ffprobe"


async def _run(cmd: list[str]) -> tuple[int, bytes]:
    """执行子进程，返回 (returncode, stdout)；ENOENT 转 PreprocessError。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise PreprocessError(
            f"找不到可执行文件: {cmd[0]}（请安装 ffmpeg 或配置 voiceprint_ffmpeg_bin）"
        ) from exc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise PreprocessError(
            f"{cmd[0]} 执行失败(rc={proc.returncode}): {stderr.decode(errors='replace')[:200]}")
    return proc.returncode or 0, stdout


async def probe(src_path: str) -> dict:
    """ffprobe 探测音频流信息：{sample_rate, channels, codec_name, format_name, duration_s}。"""
    _, stdout = await _run([
        _ffprobe_bin(), "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", src_path,
    ])
    try:
        info = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError as exc:
        raise PreprocessError(f"ffprobe 输出解析失败: {exc}") from exc
    stream: Dict[str, Any] = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    try:
        duration_s = float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_s = 0.0
    return {
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "codec_name": str(stream.get("codec_name", "")),
        "format_name": str(info.get("format", {}).get("format_name", "")),
        "duration_s": duration_s,
    }


async def mean_volume_db(src_path: str) -> Optional[float]:
    """ffmpeg volumedetect 检测平均音量（dB）；检测失败返回 None（不阻断流程）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            _ffmpeg_bin(), "-v", "info", "-i", src_path,
            "-af", "volumedetect", "-f", "null", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        raise PreprocessError(
            f"找不到可执行文件: {_ffmpeg_bin()}（请安装 ffmpeg 或配置 voiceprint_ffmpeg_bin）"
        ) from exc
    match = re.search(rb"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    return float(match.group(1)) if match else None


async def detect_silences(
    src_path: str,
    *,
    noise_db: float = -40.0,
    min_silence_s: float = 1.0,
) -> list[tuple[float, float]]:
    """silencedetect 检测静音区间，返回 [(start_s, end_s)]（检测失败返回空列表）。

    用于长音频按静音点智能截断（VAD 式切分），保证每段语义完整。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            _ffmpeg_bin(), "-v", "info", "-i", src_path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
            "-f", "null", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        raise PreprocessError(
            f"找不到可执行文件: {_ffmpeg_bin()}（请安装 ffmpeg 或配置 voiceprint_ffmpeg_bin）"
        ) from exc
    silences: list[tuple[float, float]] = []
    start: Optional[float] = None
    for line in stderr.splitlines():
        match_start = re.search(rb"silence_start:\s*(-?\d+(?:\.\d+)?)", line)
        if match_start:
            start = float(match_start.group(1))
            continue
        match_end = re.search(rb"silence_end:\s*(-?\d+(?:\.\d+)?)", line)
        if match_end and start is not None:
            silences.append((start, float(match_end.group(1))))
            start = None
    return silences


async def split_wav(src_path: str, start_s: float, end_s: float, out_path: str) -> None:
    """从 WAV 中截取 [start_s, end_s) 区间（PCM 流拷贝，帧级精确）。"""
    await _run([
        _ffmpeg_bin(), "-y", "-v", "error",
        "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
        "-i", src_path, "-c", "copy", out_path,
    ])


def is_16k_mono_wav(meta: dict) -> bool:
    """判断是否已是 16kHz 单声道 PCM WAV（可零转换直送 FunASR）。"""
    return (
        meta.get("sample_rate") == 16000
        and meta.get("channels") == 1
        and str(meta.get("codec_name", "")).startswith("pcm_")
        and "wav" in str(meta.get("format_name", ""))
    )


async def ensure_16k_mono_wav(src_path: str) -> tuple[str, bool]:
    """确保音频为 16kHz 单声道 WAV。

    Returns:
        (wav_path, converted)：converted=False 时 wav_path 即源文件（无需清理）；
        converted=True 时 wav_path 为临时文件（调用方负责删除）。

    Raises:
        PreprocessError: ffprobe/ffmpeg 不可用或转码失败。
    """
    meta = await probe(src_path)
    if is_16k_mono_wav(meta):
        return src_path, False

    fd, tmp_path = tempfile.mkstemp(prefix="voiceprint_", suffix=".wav")
    os.close(fd)
    try:
        await _run([
            _ffmpeg_bin(), "-y", "-v", "error",
            "-i", src_path, "-vn",
            "-ar", "16000", "-ac", "1", "-f", "wav", tmp_path,
        ])
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    log(f"音频预处理: {os.path.basename(src_path)} → 16k 单声道 WAV", "DEBUG", tag="音源库")
    return tmp_path, True


async def merge_to_wav(file_paths: list[str]) -> str:
    """把同一次录制的多个音频按文件名顺序合并为单个 16kHz 单声道 WAV。

    采用 ffmpeg concat demuxer（SafeRec 同源文件编码一致，无需重编码拼接）；
    单文件时退化为 ensure_16k_mono_wav 的语义（但始终产出临时文件，统一清理）。

    Returns:
        合并后的临时 WAV 路径（调用方负责删除）。

    Raises:
        PreprocessError: 无输入文件 / 合并失败。
    """
    if not file_paths:
        raise PreprocessError("合并输入为空")
    ordered = sorted(file_paths)
    if len(ordered) == 1:
        wav_path, converted = await ensure_16k_mono_wav(ordered[0])
        if converted:
            return wav_path
        # 已是 16k 单声道 WAV：复制为临时文件统一清理语义
        fd, tmp_path = tempfile.mkstemp(prefix="voiceprint_merge_", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as out, open(wav_path, "rb") as src:
                out.write(src.read())
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return tmp_path

    fd_list, list_path = tempfile.mkstemp(prefix="voiceprint_concat_", suffix=".txt")
    fd_out, out_path = tempfile.mkstemp(prefix="voiceprint_merge_", suffix=".wav")
    os.close(fd_out)
    try:
        with os.fdopen(fd_list, "w", encoding="utf-8") as f:
            for path in ordered:
                escaped = path.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        await _run([
            _ffmpeg_bin(), "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", out_path,
        ])
    except Exception:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        raise
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    log(f"音频合并: {len(ordered)} 个文件 → 16k 单声道 WAV", "DEBUG", tag="音源库")
    return out_path
