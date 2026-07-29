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
    chat_id: Optional[str] = None  # 前端多会话标识，落到 Everything.session_id 参与 scope 隔离
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

    ts = int(time.time() * 1000)
    safe_name = f"{ts}_{filename}"
    dest = sub_dir / safe_name

    def _write_upload() -> None:
        sub_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    await asyncio.to_thread(_write_upload)

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
            chat_id=req.chat_id,
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
    scope_id: str = Query("web_user", description="基础 scope（不带 chat_id 时为兼容旧版）"),
    chat_id: Optional[str] = Query(None, description="多会话 chat_id，拼接为 scope_id#{chat_id}"),
    limit: int = Query(50, ge=1, le=500),
) -> List[Dict[str, Any]]:
    effective_scope = f"{scope_id}#{chat_id}" if chat_id else scope_id
    raw = await _chat_svc.load_history(scope_id=effective_scope, limit=limit)
    return [_clean_message(m) for m in raw]


@router.get("/chats")
async def list_chats(
    user_id: str = Query("web_user"),
) -> Dict[str, Any]:
    """列出该用户在 webui 频道下出现过的所有 chat_id（基于 conversation_messages 表去重）。"""
    try:
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt is None:
            return {"chats": []}
        db = await rt.data_center.sqlite._get_db()
        # scope_id 形如 "web_user" 或 "web_user#abc123"
        cursor = await db.execute(
            "SELECT scope_id, MAX(ts_ns) AS last_ts, COUNT(*) AS cnt "
            "FROM conversation_messages "
            "WHERE scope_type='user' AND (scope_id=? OR scope_id LIKE ?) "
            "GROUP BY scope_id ORDER BY last_ts DESC",
            (user_id, f"{user_id}#%"),
        )
        rows = await cursor.fetchall()
        chats: List[Dict[str, Any]] = []
        for r in rows:
            sid = r[0]
            if "#" in sid:
                chat_id = sid.split("#", 1)[1]
            else:
                chat_id = "default"
            # 拉最近一条用户消息作为标题
            c2 = await db.execute(
                "SELECT content FROM conversation_messages "
                "WHERE scope_type='user' AND scope_id=? AND role='user' "
                "ORDER BY ts_ns DESC LIMIT 1",
                (sid,),
            )
            title_row = await c2.fetchone()
            title = ""
            if title_row:
                content = str(title_row[0] or "")
                # 去除 [tag:xxx] 前缀
                import re as _re
                content = _re.sub(r"\[(?:[^:]+):(.*?)\]", r"\1", content, flags=_re.DOTALL).strip()
                title = content[:40] or "(空消息)"
            chats.append({
                "chat_id": chat_id,
                "scope_id": sid,
                "title": title or "新会话",
                "last_ts": r[1],
                "message_count": r[2],
            })
        return {"chats": chats}
    except Exception as exc:
        return {"chats": [], "error": str(exc)}


@router.get("/bot-name")
async def get_bot_name() -> Dict[str, str]:
    return {"name": _chat_svc.get_bot_name()}


class CancelPlanRequest(BaseModel):
    chat_id: str
    plan_id: str


@router.post("/cancel-plan")
async def cancel_plan(req: CancelPlanRequest) -> Dict[str, Any]:
    """用户从前端 PlanPanel 浮窗点击"取消"：中断当前 scope 的执行并标记 plan cancelled。

    路径：
    1. 更新 MemoryStore 里的 goal status = "cancelled"（前端 PlanCard 显示）
    2. 调用 mind.interrupt(scope) 协作式中断当前 think_loop（Agent 下轮检查点停止）
    3. 发射 EVENT_PLAN_CANCELLED 事件（前端 PlanPanel 标灰）
    """
    try:
        from agent.planning.tools import _store, _find_goal, _parse_scope_chat_id
        from core.event_bus import event_bus, EVENT_PLAN_CANCELLED
        from services._runtime import get_runtime
        import json as _json

        scope = f"user_web_user#{req.chat_id}" if req.chat_id != "default" else "user_web_user"

        # 1. 更新 MemoryStore
        if _store is not None:
            entry, goal = await _find_goal(_store, req.plan_id)
            if entry is not None and goal is not None:
                goal["status"] = "cancelled"
                entry.content = _json.dumps(goal, ensure_ascii=False)
                await _store.update(entry, clear_embedding=False)

        # 2. 中断当前 scope 的执行（协作式，Agent 下轮检查点停止）
        rt = get_runtime()
        if rt is not None:
            mind = getattr(rt, "mind", None)
            if mind is not None and hasattr(mind, "interrupt"):
                mind.interrupt(scope, reason="用户取消计划")

        # 3. 发射事件
        _user_scope, chat_id = _parse_scope_chat_id(scope)
        await event_bus.emit(EVENT_PLAN_CANCELLED, {
            "scope": scope,
            "chat_id": chat_id,
            "plan_id": req.plan_id,
            "reason": "用户取消",
        })

        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


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
