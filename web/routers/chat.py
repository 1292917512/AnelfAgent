"""聊天 API 路由 -- 消息发送、文件上传、历史加载、SSE 流式推送。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.path import ConfigPaths
from services import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

_chat_svc = ChatService()

_UPLOAD_DIR = Path(ConfigPaths.UPLOAD_DIR).resolve()

_FILE_TYPES = {"image", "audio", "video", "file"}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".amr", ".opus"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv"}

# 强制以下载形式响应的扩展名（防存储型 XSS）
_ATTACHMENT_EXTS = {".html", ".htm", ".svg"}


def _upload_max_bytes() -> int:
    """上传大小上限（字节），配置 upload_max_mb，默认 50MB。"""
    try:
        from core.config import ConfigManager
        max_mb = float(ConfigManager.get("upload_max_mb", 50))
    except Exception:
        max_mb = 50.0
    return max(1, int(max_mb)) * 1024 * 1024

_sse_subscribers: List[asyncio.Queue[Dict[str, Any]]] = []


def broadcast_chat_event(event: Dict[str, Any]) -> None:
    """向所有 SSE 订阅者推送聊天事件。"""
    for q in _sse_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _setup_ui_command_bridge() -> None:
    """订阅界面命令事件并桥接到聊天 SSE 流。"""
    from core.event_bus import EVENT_UI_COMMAND, event_bus

    @event_bus.on(EVENT_UI_COMMAND, owner="webui")
    async def _forward_ui_command(payload: Dict[str, Any]) -> None:
        broadcast_chat_event({"event": "ui_command", **payload})


_setup_ui_command_bridge()


class UiAnswerRequest(BaseModel):
    ask_id: str
    answer: str


@router.post("/ui-answer")
async def ui_answer(req: UiAnswerRequest) -> Dict[str, Any]:
    """前端提交 ui_ask 弹窗的回答，解决后端挂起的提问。"""
    from entities.ui.tools import resolve_ask
    ok = resolve_ask(req.ask_id, req.answer)
    return {"status": "ok" if ok else "expired"}


class UiStateRequest(BaseModel):
    state: Dict[str, Any]


@router.post("/ui-state")
async def post_ui_state(req: UiStateRequest) -> Dict[str, str]:
    """前端上报工作台状态快照（供 ui_get_state 工具查询）。"""
    from entities.ui.tools import update_ui_state
    update_ui_state(req.state)
    return {"status": "ok"}


@router.get("/ui-state")
async def get_ui_state() -> Dict[str, Any]:
    from entities.ui.tools import get_ui_state_snapshot
    return {"state": get_ui_state_snapshot()}


class SendMessageRequest(BaseModel):
    message: str
    user_id: str = "web_user"
    user_name: str = "用户"
    images: Optional[List[str]] = None
    files: Optional[List[str]] = None


class SendMessageResponse(BaseModel):
    ok: bool = True
    error: str = ""


def _classify_file(ext: str) -> str:
    ext = ext.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    return "file"


def _resolve_media_path(file_path: str) -> str:
    """解析媒体路径：相对路径优先按当前路径，其次按工作区根目录解析。"""
    if not file_path or file_path.startswith(("http://", "https://", "/api/")):
        return file_path
    if os.path.isabs(file_path) or os.path.exists(file_path):
        return file_path
    try:
        from entities.filesystem.tools import _safe_path
        resolved = _safe_path(file_path)
        if os.path.exists(resolved):
            return resolved
    except Exception:
        pass
    return file_path


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload a file to workspace/uploads/{type}/, return metadata."""
    from fastapi import HTTPException

    filename = os.path.basename(file.filename or f"upload_{int(time.time())}")
    if not filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    ext = Path(filename).suffix.lower()
    file_type = _classify_file(ext)

    max_bytes = _upload_max_bytes()
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, f"File too large (limit {max_bytes // (1024 * 1024)}MB)")

    sub_dir = _UPLOAD_DIR / file_type
    sub_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time() * 1000)
    safe_name = f"{ts}_{filename}"
    dest = sub_dir / safe_name

    with open(dest, "wb") as f:
        f.write(content)

    return {
        "path": str(dest),
        "name": filename,
        "type": file_type,
        "size": len(content),
        "url": f"/api/chat/files/{file_type}/{safe_name}",
    }


@router.get("/files/{file_type}/{filename}")
async def serve_uploaded_file(file_type: str, filename: str) -> Any:
    """Serve an uploaded file."""
    from fastapi import HTTPException
    from starlette.responses import FileResponse
    if file_type not in _FILE_TYPES:
        raise HTTPException(404, "File not found")
    fp = (_UPLOAD_DIR / file_type / os.path.basename(filename)).resolve()
    if not str(fp).startswith(str(_UPLOAD_DIR) + os.sep) or not fp.is_file():
        raise HTTPException(404, "File not found")
    headers = None
    if fp.suffix.lower() in _ATTACHMENT_EXTS:
        headers = {"Content-Disposition": "attachment"}
    return FileResponse(str(fp), headers=headers)


@router.post("/send", response_model=SendMessageResponse)
async def send_message(req: SendMessageRequest) -> SendMessageResponse:
    try:
        from agent.llm.types import ImageContent
        from agent.channel.schemas import MessageSegment, SegmentType

        images = None
        if req.images:
            images = []
            for img in req.images:
                # Convert API URL back to local path for consistent path-based handling
                if img.startswith("/api/chat/files/"):
                    parts = img.replace("/api/chat/files/", "").split("/", 1)
                    if len(parts) == 2:
                        local = str(_UPLOAD_DIR / parts[0] / parts[1])
                        if Path(local).exists():
                            img = local
                if img.startswith("http"):
                    images.append(ImageContent(data=img, is_url=True))
                else:
                    images.append(ImageContent(data=img))

        media_segments = None
        if req.files:
            media_segments = []
            for file_path in req.files:
                file_path = _resolve_media_path(file_path)
                ext = Path(file_path).suffix.lower()
                ftype = _classify_file(ext)
                seg_type_map = {
                    "image": SegmentType.IMAGE,
                    "audio": SegmentType.AUDIO,
                    "video": SegmentType.VIDEO,
                    "file": SegmentType.FILE,
                }
                seg = MessageSegment(
                    type=seg_type_map.get(ftype, SegmentType.FILE),
                    file_path=file_path,
                    file_name=Path(file_path).name,
                    url=file_path if file_path.startswith("/api/") else "",
                )
                if ftype == "image" and not images:
                    images = []
                if ftype == "image":
                    images.append(ImageContent(data=file_path, is_url=False))
                else:
                    media_segments.append(seg)

        text = req.message
        if req.files:
            file_descs = [f"[{_classify_file(Path(fp).suffix.lower())}:{fp}]" for fp in req.files]
            if file_descs:
                text = text + "\n" + " ".join(file_descs) if text else " ".join(file_descs)

        await _chat_svc.send_message(
            text,
            images=images,
            media_segments=media_segments if media_segments else None,
            user_id=req.user_id,
            user_name=req.user_name,
            adapter_key="webui",
        )
        return SendMessageResponse()
    except Exception as e:
        return SendMessageResponse(ok=False, error=str(e))


def _clean_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """清理消息中的内部标签，返回干净的前端展示数据。"""
    import re
    content = str(msg.get("content", ""))
    content = re.sub(r"\[(?:[^:]+):(.*?)\]", r"\1", content, flags=re.DOTALL)
    content = content.strip()
    result: Dict[str, Any] = {
        "role": msg.get("role", ""),
        "content": content,
    }
    if "id" in msg:
        result["id"] = msg["id"]
    ts_ns = msg.get("ts_ns")
    if ts_ns and isinstance(ts_ns, (int, float)) and ts_ns > 0:
        import datetime
        ts = ts_ns / 1e9 if ts_ns > 1e15 else ts_ns
        result["timestamp"] = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    return result


@router.get("/history")
async def get_history(
    scope_id: str = Query("web_user"),
    limit: int = Query(50, ge=1, le=500),
) -> List[Dict[str, Any]]:
    raw = await _chat_svc.load_history(scope_id=scope_id, limit=limit)
    return [_clean_message(m) for m in raw]


@router.get("/bot-name")
async def get_bot_name() -> Dict[str, str]:
    return {"name": _chat_svc.get_bot_name()}


@router.get("/stream")
async def chat_stream(request: Request) -> EventSourceResponse:
    """SSE 端点：推送聊天消息事件。"""
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=256)
    _sse_subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": msg.get("event", "message"), "data": json.dumps(msg, ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _sse_subscribers.remove(queue)

    return EventSourceResponse(event_generator())
