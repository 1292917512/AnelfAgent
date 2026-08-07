"""FunASR 服务 HTTP 客户端（实体主动拉取通道）。

对接契约（用户自部署的 FunASR 服务实现）：
    POST {endpoint}/transcribe
    请求：multipart/form-data
      - file：音频文件（16kHz 单声道 WAV 最佳）
      - source_time（可选）：音频原始录制时刻（epoch 毫秒或 ISO8601），
        服务据此为每段换算绝对时间
    响应：{"segments": [{"start_ms": 0, "end_ms": 3200, "text": "...",
           "vector": [192 floats],
           "abs_start_ms": 1786005000000, "abs_end_ms": 1786005003200}]}
    abs_* 为 epoch 毫秒绝对时间（source_time + 段内偏移），缺省时由调用方
    按录制基准时间 + 段内偏移自行换算（旧契约完全兼容）。

endpoint 与超时经实体配置 voiceprint_funasr_endpoint / voiceprint_funasr_timeout 调整；
未配置 endpoint 时所有方法抛 FunAsrNotConfigured，由调用方转为友好错误。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx

from core.config import get_config, get_config_float
from core.log import log

from . import ffmpeg as audio_pre


class FunAsrNotConfigured(RuntimeError):
    """FunASR endpoint 未配置。"""


class FunAsrError(RuntimeError):
    """FunASR 服务调用失败（网络/状态码/响应格式）。"""


async def _ensure_wav(audio_path: str) -> tuple[str, bool]:
    """预处理为 16k 单声道 WAV（独立函数便于测试替换）。"""
    return await audio_pre.ensure_16k_mono_wav(audio_path)


def _endpoint_config() -> str:
    return str(get_config("voiceprint_funasr_endpoint", "") or "").strip().rstrip("/")


def _endpoint() -> str:
    endpoint = _endpoint_config()
    if not endpoint:
        raise FunAsrNotConfigured(
            "未配置 FunASR 服务地址（voiceprint_funasr_endpoint），"
            "请在实体详情页配置中填写，如 http://nas:10095")
    return endpoint


def is_configured() -> bool:
    """FunASR 服务是否已配置（工具 check_fn 门控用）。"""
    return bool(_endpoint_config())


async def transcribe(audio_path: str, source_time: str = "") -> List[Dict[str, Any]]:
    """调用 FunASR 服务转写音频，返回标准化 segments 列表。

    Args:
        audio_path: 本地音频文件绝对路径。
        source_time: 音频原始录制时刻（epoch 毫秒或 ISO8601 字符串），
            非空时透传给服务，响应段将携带 abs_start_ms/abs_end_ms 绝对时间。

    Returns:
        [{"start_ms": int, "end_ms": int, "text": str,
          "vector": list[float] | None,
          "abs_start_ms": int | None, "abs_end_ms": int | None}]

    Raises:
        FunAsrNotConfigured: endpoint 未配置。
        FunAsrError: 网络/状态码/响应格式错误。
    """
    endpoint = _endpoint()
    timeout = max(10.0, get_config_float("voiceprint_funasr_timeout", 120.0))
    if not os.path.isfile(audio_path):
        raise FunAsrError(f"音频文件不存在: {audio_path}")

    # 统一预处理为 16k 单声道 WAV（服务端对 m4a/amr/wma 等容器支持不一）；
    # ffmpeg 不可用/探测失败时回退原始文件直传（wav/mp3 原生可识别）
    upload_path = audio_path
    converted = False
    try:
        try:
            upload_path, converted = await _ensure_wav(audio_path)
        except audio_pre.PreprocessError as exc:
            log(f"音频预处理跳过（原始文件直传）: {exc}", "DEBUG", tag="音源库")

        resp = await _post_with_retry(endpoint, upload_path, audio_path,
                                      source_time, timeout, converted)
    finally:
        if converted:
            try:
                os.unlink(upload_path)
            except OSError:
                pass

    if resp.status_code != 200:
        raise FunAsrError(f"FunASR 服务返回 {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
        raw_segments = payload["segments"]
    except Exception as exc:
        raise FunAsrError(f"FunASR 响应格式不符契约: {exc}") from exc

    segments: List[Dict[str, Any]] = []
    for seg in raw_segments:
        vector = seg.get("vector")
        segments.append({
            "start_ms": int(seg.get("start_ms", 0)),
            "end_ms": int(seg.get("end_ms", 0)),
            "text": str(seg.get("text", "")),
            "vector": [float(x) for x in vector] if isinstance(vector, list) else None,
            "abs_start_ms": _opt_int(seg.get("abs_start_ms")),
            "abs_end_ms": _opt_int(seg.get("abs_end_ms")),
        })
    log(f"FunASR 转写完成: {os.path.basename(audio_path)} → {len(segments)} 段", tag="音源库")
    return segments


def _opt_int(value: Any) -> Optional[int]:
    """可选整数字段解析（None/非法值 → None）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _post_with_retry(
    endpoint: str,
    upload_path: str,
    audio_path: str,
    source_time: str,
    timeout: float,
    converted: bool,
) -> httpx.Response:
    """上传转写请求：服务端瞬时转换失败（并发临时文件竞争 rc=183 类）自动重试一次。

    source_time 经 data= 发送（httpx DataField 普通表单字段）；服务端按字段名
    解析，与部件顺序无关。注意不可放进 files 列表——裸字符串会被 httpx 当作
    文件字段（自动补 filename="upload"），服务端可能误当音频覆盖真文件。
    """
    upload_name = (
        f"{os.path.splitext(os.path.basename(audio_path))[0]}.wav"
        if converted else os.path.basename(audio_path)
    )
    data = {"source_time": source_time} if source_time.strip() else None
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with open(upload_path, "rb") as f:
                    files = {"file": (upload_name, f)}
                    resp = await client.post(
                        f"{endpoint}/transcribe", files=files, data=data)
        except httpx.HTTPError as exc:
            # 服务重启/瞬时网络故障：重试一次
            if attempt == 0:
                log(f"FunASR 连接失败，1s 后重试: {exc}", "WARNING", tag="音源库")
                await asyncio.sleep(1.0)
                continue
            raise FunAsrError(f"FunASR 服务不可达: {exc}") from exc
        if resp.status_code == 200:
            return resp
        transient = resp.status_code in (408, 429, 500, 502, 503, 504) or (
            resp.status_code == 400 and "convert failed" in resp.text)
        if not transient or attempt == 1:
            return resp
        last_exc = FunAsrError(f"FunASR 服务返回 {resp.status_code}")
        log(f"FunASR 瞬时失败（{resp.status_code}），1s 后重试: {resp.text[:120]}",
            "WARNING", tag="音源库")
        await asyncio.sleep(1.0)
    assert last_exc is not None
    raise last_exc
