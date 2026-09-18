"""人脸识别引擎 HTTP 客户端（对接自部署的 InsightFace 服务）。

对接契约（用户自部署的 FaceEngine 服务实现，参考 deploy/face_server/）：
    GET  {endpoint}/health
      → {"status":"ok","model":"buffalo_l","dim":512,"device":"cuda","version":"1.0"}
    POST {endpoint}/extract
      multipart: file（图片）+ 可选 min_det_score / max_faces
      → {"width":…,"height":…,"faces":[{"bbox":[x,y,w,h],"det_score":0.92,
          "pose":{"pitch":…,"yaw":…,"roll":…},"vector":[512 floats]}]}
      faces 按 det_score 降序，vector 为 L2 归一化（余弦=点积）。
    错误响应统一 {"error":{"code","message"}}，码表：
      INVALID_IMAGE(400) / IMAGE_TOO_LARGE(413) / MODEL_NOT_READY(503)
      / ENGINE_ERROR(500)

endpoint 走组件凭据中心（provider_keys "face".face_endpoint）；
未配置时所有方法抛 FaceEngineNotConfigured，由调用方转为友好错误。
业务链路（ingest/worker）一律 fail-open：引擎不可达只记日志不阻塞。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx

from core.config import get_config_float
from core.log import log

from .schemas import EngineHealth, ExtractResult, FaceDetection, FacePose

_LOG_TAG = "人脸"

# 引擎错误码 → (是否可重试, 友好提示)；网络/超时类在客户端就地归为可重试
_ERROR_HINTS: Dict[str, tuple[bool, str]] = {
    "INVALID_IMAGE": (False, "图片无法解码（损坏或非常规格式）"),
    "IMAGE_TOO_LARGE": (False, "图片超过服务端大小上限"),
    "MODEL_NOT_READY": (True, "识别模型仍在加载，稍后重试"),
    "ENGINE_ERROR": (True, "识别服务内部错误"),
}


class FaceEngineNotConfigured(RuntimeError):
    """FaceEngine endpoint 未配置。"""


class FaceEngineError(RuntimeError):
    """FaceEngine 服务调用失败（网络/状态码/响应格式）。"""

    def __init__(self, message: str, *, code: str = "NETWORK", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def endpoint_config() -> str:
    """服务地址（凭据中心单源；配置经组件凭据面板/AI 工具/文件）。"""
    from core.provider_keys import get_provider_key

    return get_provider_key("face", field="face_endpoint").rstrip("/")


def _endpoint() -> str:
    endpoint = endpoint_config()
    if not endpoint:
        raise FaceEngineNotConfigured(
            "未配置人脸识别服务地址（组件凭据 face），"
            "请在视觉页 → 组件凭据配置，如 http://gpu-host:10097")
    return endpoint


def is_configured() -> bool:
    """人脸引擎是否已配置（工具 check_fn 门控用）。"""
    return bool(endpoint_config())


_PROBE_TTL_OK = 30.0
_PROBE_TTL_FAIL = 60.0
_probe_cache: tuple[float, bool] = (0.0, False)


async def probe_available() -> bool:
    """服务真实可达性（未配置或不可达均为 False；短 TTL 缓存防链解析抖动）。

    探测发 GET /health，任何 HTTP 应答（含 404）都视为可达——
    目的只是确认服务进程活着，具体业务正确性由实际调用报错。
    """
    global _probe_cache
    endpoint = endpoint_config()
    if not endpoint:
        return False
    now = time.monotonic()
    cached_at, cached_ok = _probe_cache
    if now - cached_at < (_PROBE_TTL_OK if cached_ok else _PROBE_TTL_FAIL):
        return cached_ok
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            await client.get(f"{endpoint}/health")
        ok = True
    except Exception:
        ok = False
    _probe_cache = (now, ok)
    return ok


def reset_probe_cache() -> None:
    """清空探测缓存（测试/配置变更后用）。"""
    global _probe_cache
    _probe_cache = (0.0, False)


async def health(refresh: bool = False) -> Optional[EngineHealth]:
    """读取引擎健康详情（模型/维度/设备）；不可达返回 None。"""
    if not endpoint_config():
        return None
    if refresh:
        reset_probe_cache()
    try:
        async with _client(timeout=5.0) as client:
            resp = await client.get(f"{_endpoint()}/health")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return EngineHealth(
            status=str(data.get("status", "")),
            model=str(data.get("model", "")),
            dim=int(data.get("dim", 0) or 0),
            device=str(data.get("device", "")),
            version=str(data.get("version", "")),
        )
    except Exception:
        return None


def _client(timeout: float) -> httpx.AsyncClient:
    # 固定内网服务：禁用环境代理（防系统代理劫持致 502）
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _engine_timeout() -> float:
    return max(5.0, get_config_float("face_engine_timeout", 30.0))


def _parse_error(resp: httpx.Response) -> FaceEngineError:
    """把结构化错误应答解析为 FaceEngineError（契约外应答按原文截断）。"""
    code, message = "", ""
    try:
        payload = resp.json()
        err = payload.get("error") or {}
        code = str(err.get("code", ""))
        message = str(err.get("message", ""))
    except Exception:
        pass
    if not code:
        code = f"HTTP_{resp.status_code}"
    retryable, hint = _ERROR_HINTS.get(code, (resp.status_code >= 500, ""))
    detail = message or resp.text[:200]
    return FaceEngineError(
        f"人脸引擎返回 {code}: {detail}" + (f"（{hint}）" if hint else ""),
        code=code, retryable=retryable)


async def extract_faces(
    image_path: str,
    *,
    min_det_score: float = 0.0,
    max_faces: int = 0,
) -> ExtractResult:
    """检测并提取图片中全部人脸（服务端一次完成检测/对齐/嵌入）。

    Raises:
        FaceEngineNotConfigured: endpoint 未配置。
        FaceEngineError: 网络/状态码/响应格式错误（retryable 标注可重试性）。
    """
    endpoint = _endpoint()
    if not os.path.isfile(image_path):
        raise FaceEngineError(f"图片文件不存在: {image_path}",
                              code="NO_FILE", retryable=False)
    timeout = _engine_timeout()
    data: Dict[str, Any] = {}
    if min_det_score > 0:
        data["min_det_score"] = f"{min_det_score:.3f}"
    if max_faces > 0:
        data["max_faces"] = str(max_faces)

    resp = await _post_with_retry(endpoint, image_path, data, timeout)
    if resp.status_code != 200:
        raise _parse_error(resp)
    try:
        payload = resp.json()
        faces = [
            FaceDetection(
                bbox=[float(v) for v in f.get("bbox", [])],
                det_score=float(f.get("det_score", 0.0)),
                pose=FacePose(**{k: float(f.get("pose", {}).get(k, 0.0))
                                 for k in ("pitch", "yaw", "roll")}),
                vector=[float(v) for v in f.get("vector", [])],
            )
            for f in payload.get("faces", [])
        ]
    except Exception as exc:
        raise FaceEngineError(f"人脸引擎响应格式不符契约: {exc}",
                              code="BAD_RESPONSE", retryable=False) from exc
    return ExtractResult(
        width=int(payload.get("width", 0) or 0),
        height=int(payload.get("height", 0) or 0),
        faces=faces,
    )


async def _post_with_retry(
    endpoint: str, image_path: str, data: Dict[str, Any], timeout: float,
) -> httpx.Response:
    """上传提取请求：网络瞬时失败/服务端 5xx 自动重试一次。"""
    upload_name = os.path.basename(image_path)
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            async with _client(timeout) as client:
                with open(image_path, "rb") as f:
                    files = {"file": (upload_name, f)}
                    resp = await client.post(
                        f"{endpoint}/extract", files=files, data=data or None)
        except httpx.HTTPError as exc:
            if attempt == 0:
                log(f"人脸引擎连接失败，1s 后重试: {exc}", "WARNING", tag=_LOG_TAG)
                await asyncio.sleep(1.0)
                continue
            raise FaceEngineError(f"人脸引擎服务不可达: {exc}") from exc
        if resp.status_code == 200:
            return resp
        transient = resp.status_code in (408, 429, 500, 502, 503, 504)
        if not transient or attempt == 1:
            return resp
        last_exc = _parse_error(resp)
        log(f"人脸引擎瞬时失败（{resp.status_code}），1s 后重试: {resp.text[:120]}",
            "WARNING", tag=_LOG_TAG)
        await asyncio.sleep(1.0)
    assert last_exc is not None
    raise last_exc
