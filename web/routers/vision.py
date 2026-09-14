"""视觉能力 API 路由 — 视觉页签与外部画面推送的数据面（/api/vision）。

POST /push 是外部视觉源的注入端点（本机其他进程、未来桌面壳的截帧桥）：
base64 图片落盘后经缓冲统一入口汇入，走与本地捕获相同的判变与注入路径。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import FileResponse

from services.vision import VisionServiceFacade

router = APIRouter(prefix="/vision", tags=["vision"])

_vision = VisionServiceFacade()


class PushFrameRequest(BaseModel):
    source: str
    """外部源名（缓冲中记为 external:<source>）。"""
    image_base64: str
    mime_type: str = "image/png"
    captured_at: Optional[float] = None


class WatchRequest(BaseModel):
    action: str = "status"  # start / stop / status
    source: str = "screen"
    interval: float = 0


@router.get("/status")
async def status() -> Dict[str, Any]:
    """监视状态 + 各源最新帧元信息 + 画面注入情况。"""
    return _vision.status()


@router.get("/sources")
async def sources() -> Dict[str, Any]:
    """视觉源组件清单（核心注册表 + 监视状态）。"""
    return _vision.sources()


@router.get("/capabilities")
async def capabilities() -> Dict[str, Any]:
    """视觉能力（理解/图生成/图编辑/视频）的提供者状态与生效优先级链。"""
    return _vision.capabilities()


_LATEST_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


@router.get("/latest")
async def latest(source: str = "") -> FileResponse:
    """最新一帧画面预览（默认全局最新；source 指定源）。"""
    import os
    path = _vision.latest_frame(source)
    if path is None:
        raise HTTPException(404, "尚无画面")
    mime = _LATEST_MIME.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    return FileResponse(path, media_type=mime)


@router.post("/watch")
async def watch(req: WatchRequest) -> Dict[str, Any]:
    """监视开关（页签控制；与 vision_watch 工具同语义）。"""
    try:
        return await _vision.watch(req.action, req.source, req.interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/push")
async def push(req: PushFrameRequest) -> Dict[str, Any]:
    """外部视觉源推送一帧（base64 图片 → 缓冲统一入口）。"""
    try:
        return await _vision.push_frame(
            req.source, req.image_base64, req.mime_type, req.captured_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(413, str(exc)) from exc
