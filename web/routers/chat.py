"""聊天 API 路由 -- 消息发送、文件上传、历史加载、SSE 流式推送。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from services import ChatService, UiService
from services.chat import UPLOAD_DIR as _UPLOAD_DIR
from services.chat import classify_file_type as _classify_file
from web.routers._errors import server_error

router = APIRouter(prefix="/chat", tags=["chat"])

_chat_svc = ChatService()
_ui_svc = UiService()

# 消息内容内部标签（[tag:xxx]）剥离正则，历史清洗与会话标题共用；
# 键为单词字符（与 tag_label 生成一致），值禁止跨 [、] 与换行，
# 防止多行正文（执行摘要等）被错误配对吞掉
_TAG_PREFIX_RE = re.compile(r"\[(?:\w+):([^\[\]\n]*)\]")

_FILE_TYPES = {"image", "audio", "video", "file"}

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

from core import sse_hub  # noqa: E402  订阅注册中心在 core 层（频道侧也要用）


def broadcast_chat_event(event: Dict[str, Any]) -> None:
    """向所有 SSE 订阅者推送聊天事件。"""
    sse_hub.broadcast(event)


def _setup_chat_broadcast_bridge() -> None:
    """订阅聊天广播事件并桥接到 SSE 流（WebUI 频道经此事件推帧，不反向依赖 web 层）。"""
    from core.event_bus import EVENT_CHAT_BROADCAST, event_bus

    @event_bus.on(EVENT_CHAT_BROADCAST, owner="webui")
    async def _forward_chat_broadcast(payload: Dict[str, Any]) -> None:
        broadcast_chat_event(payload)


def _setup_ui_command_bridge() -> None:
    """订阅界面命令事件并桥接到聊天 SSE 流。"""
    from core.event_bus import EVENT_UI_COMMAND, event_bus

    @event_bus.on(EVENT_UI_COMMAND, owner="webui")
    async def _forward_ui_command(payload: Dict[str, Any]) -> None:
        broadcast_chat_event({"event": "ui_command", **payload})


def _setup_share_event_bridge() -> None:
    """订阅分享创建事件并桥接到聊天 SSE 流（前端渲染分享卡片）。"""
    from core.event_bus import EVENT_SHARE_CREATED, event_bus

    @event_bus.on(EVENT_SHARE_CREATED, owner="webui")
    async def _forward_share_created(payload: Dict[str, Any]) -> None:
        broadcast_chat_event({"event": "share", **payload})


_setup_chat_broadcast_bridge()
_setup_ui_command_bridge()
_setup_share_event_bridge()


class UiAnswerRequest(BaseModel):
    ask_id: str
    answer: str


@router.post("/ui-answer")
async def ui_answer(req: UiAnswerRequest) -> Dict[str, Any]:
    """前端提交 ui_ask 弹窗的回答，解决后端挂起的提问。"""
    ok = _ui_svc.resolve_ask(req.ask_id, req.answer)
    return {"status": "ok" if ok else "expired"}


class UiStateRequest(BaseModel):
    state: Dict[str, Any]


@router.post("/ui-state")
async def post_ui_state(req: UiStateRequest) -> Dict[str, str]:
    """前端上报工作台状态快照（供 ui_get_state 工具查询）。"""
    _ui_svc.update_ui_state(req.state)
    return {"status": "ok"}


@router.get("/ui-state")
async def get_ui_state() -> Dict[str, Any]:
    return {"state": _ui_svc.get_ui_state_snapshot()}


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


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload a file to workspace/uploads/{type}/, return metadata."""
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
        await _chat_svc.send_web_message(
            req.message,
            images=req.images,
            files=req.files,
            user_id=req.user_id,
            user_name=req.user_name,
            chat_id=req.chat_id,
        )
        return SendMessageResponse()
    except Exception as e:
        return SendMessageResponse(ok=False, error=str(e))


def _clean_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """清理消息中的内部标签，返回干净的前端展示数据。

    清洗顺序：元数据标签（time/uid 等）与功能性标签（media_file 等）整段删除
    ——保留值只会拼出乱码前缀；其余 [k:v] 标签保留值（兼容旧语义）。

    kind 标记供前端结构化渲染：
    - tool_summary：[已执行操作摘要] 工具执行记录 → 折叠工具卡片
    - system_notice：[系统]/[执行步骤] 等系统元消息 → 居中细条
    """
    from core.tags import strip_functional_tags, strip_message_meta_tags

    content = str(msg.get("content", ""))
    content = strip_message_meta_tags(content)
    content = strip_functional_tags(content)
    # kind 判定先于通用标签剥离：结构化前缀一旦识别即锁定，
    # 避免正文中的类标签片段干扰后续清洗导致前缀丢失
    head = content.strip()
    kind: Optional[str] = None
    if head.startswith("[已执行操作摘要]"):
        kind = "tool_summary"
    elif head.startswith(("[系统]", "[执行步骤]")):
        kind = "system_notice"
    content = _TAG_PREFIX_RE.sub(r"\1", content).strip()
    result: Dict[str, Any] = {
        "role": msg.get("role", ""),
        "content": content,
    }
    if kind:
        result["kind"] = kind
    if "id" in msg:
        result["id"] = msg["id"]
    ts_ns = msg.get("ts_ns")
    if ts_ns and isinstance(ts_ns, (int, float)) and ts_ns > 0:
        import datetime
        ts = ts_ns / 1e9 if ts_ns > 1e15 else ts_ns
        # ts：epoch 秒（前端时间线合排 plan/delegation 卡片用，须与消息同源）
        result["ts"] = ts
        result["timestamp"] = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    return result


@router.get("/history")
async def get_history(
    scope_id: str = Query("webui:web_user", description="基础 scope（不含 adapter 前缀时自动补 webui:）"),
    chat_id: Optional[str] = Query(None, description="多会话 chat_id，拼接为 scope_id#{chat_id}"),
    limit: int = Query(50, ge=1, le=500),
    before_id: Optional[int] = Query(None, description="分页游标：仅取 id 早于该值的消息"),
) -> List[Dict[str, Any]]:
    base_scope = _normalize_web_scope_id(scope_id)
    effective_scope = f"{base_scope}#{chat_id}" if chat_id else base_scope
    raw = await _chat_svc.load_history(scope_id=effective_scope, limit=limit, before_id=before_id)
    return [_clean_message(m) for m in raw]


def _normalize_web_scope_id(scope_id: str) -> str:
    """webui 历史查询的 scope_id 归一化：裸 user_id 自动补 adapter 前缀（兼容旧前端）。"""
    sid = (scope_id or "").strip()
    if not sid:
        return "webui:web_user"
    if ":" in sid.split("#", 1)[0]:
        return sid
    return f"webui:{sid}"


@router.get("/chats")
async def list_chats(
    user_id: str = Query("web_user"),
) -> Dict[str, Any]:
    """列出该用户在 webui 频道下出现过的所有 chat_id（基于 conversation_messages 表去重）。"""
    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is None:
        return {"chats": []}
    try:
        sessions = await rt.data_center.sqlite.list_user_chat_sessions(
            _normalize_web_scope_id(user_id)
        )
    except Exception as exc:
        raise server_error("查询会话列表", exc) from exc
    chats: List[Dict[str, Any]] = []
    for s in sessions:
        sid = s["scope_id"]
        # scope_id 形如 "webui:web_user" 或 "webui:web_user#abc123"
        chat_id = sid.split("#", 1)[1] if "#" in sid else "default"
        title = "新会话"
        raw_content = s.get("last_user_content")
        if raw_content is not None:
            # 最近一条用户消息作为标题（与历史清洗同规则：元数据/功能标签整段剥离）
            title = _clean_message({"content": str(raw_content)})["content"][:40] or "(空消息)"
        chats.append({
            "chat_id": chat_id,
            "scope_id": sid,
            "title": title,
            "last_ts": s["last_ts"],
            "message_count": s["message_count"],
        })
    return {"chats": chats}


@router.get("/bot-name")
async def get_bot_name() -> Dict[str, str]:
    return {"name": _chat_svc.get_bot_name()}


class CancelPlanRequest(BaseModel):
    chat_id: str
    plan_id: str


@router.post("/cancel-plan")
async def cancel_plan(req: CancelPlanRequest) -> Dict[str, Any]:
    """用户从前端 PlanPanel 浮窗点击"取消"：标记 cancelled + interrupt scope + 发射事件。"""
    ok = await _chat_svc.cancel_plan(req.chat_id, req.plan_id)
    if not ok:
        return {"status": "error", "error": "plan 不存在或已结束"}
    return {"status": "ok"}


class InterruptRequest(BaseModel):
    chat_id: str = "default"


@router.post("/interrupt")
async def interrupt_chat(req: InterruptRequest) -> Dict[str, Any]:
    """前端"停止生成"按钮：协作式中断当前回复 + 取消该会话运行中的子代理。

    中断是协作式的（下一轮检查点安全收束），子代理取消会即时终止其执行任务，
    delegate_task 工具结果携带 user_cancel 归因提示 AI 不要自动重试。
    """
    return _chat_svc.interrupt_chat(req.chat_id)


@router.get("/delegations")
async def list_delegations(
    chat_id: str = Query("default"),
) -> Dict[str, Any]:
    """列出该会话运行中的子代理委托（前端刷新后恢复 DelegationCard 进度）。"""
    return {"running": _chat_svc.list_delegations(chat_id)}


@router.post("/delegations/{delegation_id}/cancel")
async def cancel_delegation(delegation_id: str) -> Dict[str, Any]:
    """取消运行中的子代理委托（DelegationCard 取消按钮）。"""
    ok = _chat_svc.cancel_delegation(delegation_id)
    if ok is None:
        return {"status": "error", "error": "runtime 未就绪"}
    if not ok:
        return {"status": "error", "error": "委托不存在或已结束"}
    return {"status": "ok"}


@router.get("/stream")
async def chat_stream(request: Request) -> EventSourceResponse:
    """SSE 端点：推送聊天消息事件。"""
    queue: asyncio.Queue[Dict[str, Any]] = sse_hub.subscribe()

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
            sse_hub.unsubscribe(queue)

    return EventSourceResponse(event_generator())
