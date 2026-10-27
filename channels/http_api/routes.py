"""HTTP 消息、会话执行控制与视觉路由；入站统一经 ChannelManager 分发。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageKind,
    notification_channel,
    notification_sender,
)

from .schemas import ChatRequest, ChatResponse, ExecutionRequest, SessionTarget, VisionPushRequest
from .transport import portal_dependencies

if TYPE_CHECKING:
    from .adapter import HttpApiChannel


def build_router(adapter: HttpApiChannel) -> APIRouter:
    """装配消息与视觉端点，沿用门户统一认证和请求体边界。"""
    from .adapter import _StreamSub

    router = APIRouter()
    @router.post("/api/chat", response_model=ChatResponse, tags=["conversation"], dependencies=portal_dependencies())
    async def chat(req: ChatRequest, request: Request) -> ChatResponse:
        error = adapter._validate_chat_request(req)
        if error:
            return ChatResponse(request_id=req.request_id, status="error", error=error)
        message_id = req.message_id or req.request_id
        dedup_key = json.dumps([req.scope, message_id])
        if adapter._dedup.is_duplicate(dedup_key):
            return ChatResponse(request_id=req.request_id, status="duplicate", error="消息已受理或正在受理")
        kind = MessageKind(req.kind)
        notification = kind == MessageKind.NOTIFICATION
        fut: asyncio.Future[str] | None = None
        accepted = False
        previous_callback = adapter._callbacks.get(req.scope)
        try:
            segments = await adapter._build_inbound_segments(req)
            if req.callback_url and not notification:
                adapter._register_callback(req.chat_id, req.callback_url, req.session_id, req.channel_type)
            if not req.async_mode and not notification:
                fut = adapter._expect_reply(req.chat_id, req.session_id, req.channel_type)
            sender = notification_sender(adapter.channel_id, "HTTP API") if notification else AdapterUser(
                platform=adapter.channel_id, user_id=req.user_id, user_name=req.user_name or req.user_id,
            )
            target = notification_channel("HTTP API") if notification else AdapterChannel(
                channel_id=req.chat_id, channel_type=ChannelType(req.channel_type),
            )
            content = (f"来自 {req.user_name or req.user_id}({req.user_id}) 的通知：\n{req.message}"
                       if notification else req.message)
            inbound = AdapterMessage(
                message_id=message_id, sender=sender, channel=target, content=content,
                segments=segments, kind=kind, is_to_me=req.to_me,
                reply_to_id=req.reply_to_id, reply_content=req.reply_content,
                session_id="" if notification else req.session_id,
            )
            from agent.channel.manager import get_channel_manager
            await get_channel_manager().dispatch_inbound(adapter, inbound)
            accepted = True
            if fut is None:
                return ChatResponse(request_id=req.request_id, status="accepted")
            timeout = adapter.config.reply_timeout
            try:
                reply = await asyncio.wait_for(fut, timeout=timeout)
                return ChatResponse(request_id=req.request_id, reply=reply)
            except asyncio.TimeoutError:
                return ChatResponse(request_id=req.request_id, status="timeout", error=f"Agent 回复超时（{timeout}s）")
            except RuntimeError as exc:
                return ChatResponse(request_id=req.request_id, status="error", error=str(exc))
        except Exception as exc:
            from core.tool_errors import error_from_exception
            return ChatResponse(request_id=req.request_id, status="error",
                                error=json.loads(error_from_exception(exc))["error"])
        finally:
            if not accepted:
                adapter._dedup.discard(dedup_key)
                if req.callback_url and adapter._callbacks.get(req.scope) == req.callback_url:
                    if previous_callback is None:
                        adapter._callbacks.pop(req.scope, None)
                    else:
                        adapter._callbacks[req.scope] = previous_callback
            if fut is not None:
                if adapter._pending_replies.get(req.scope) is fut:
                    adapter._pending_replies.pop(req.scope, None)
                if not fut.done():
                    fut.cancel()
                elif not fut.cancelled():
                    fut.exception()

    @router.post("/api/sessions/control", tags=["conversation"], dependencies=portal_dependencies())
    async def control(req: ExecutionRequest, request: Request) -> dict[str, Any]:
        """停止只发出协作式取消请求；继续是一条经标准入站提交的新消息。"""
        if req.action == "resume":
            if req.run_id:
                raise HTTPException(422, "resume 使用会话目标和 request_id；run_id 仅用于精确停止")
            response = await chat(ChatRequest(
                user_id=req.user_id, group_id=req.group_id, session_id=req.session_id,
                message=req.message, message_id=f"resume-{req.request_id}",
                request_id=req.request_id, async_mode=True,
            ), request)
            return {**response.model_dump(), "scope": req.scope}
        from agent.execution_control import request_execution_stop
        from core.latebind import WireError
        try:
            result = await request_execution_stop(scope=req.scope, run_id=req.run_id, reason=req.reason)
        except WireError as exc:
            raise HTTPException(503, "执行控制尚未就绪") from exc
        return {**result, "scope": req.scope, "run_id": req.run_id}

    @router.get("/api/sessions/history", tags=["conversation"], dependencies=portal_dependencies())
    async def history(
        user_id: str = "api_user", group_id: str = "", session_id: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        before_id: int | None = Query(default=None, ge=1),
        after_id: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        """读取原会话持久化历史，不触发思考、召回或已读水位变更。"""
        from agent.channel.output_tools import conversation_data_port
        if not conversation_data_port.bound:
            raise HTTPException(503, "会话存储尚未就绪")
        if before_id is not None and after_id is not None:
            raise HTTPException(422, "before_id 与 after_id 不能同时使用")
        target = SessionTarget(user_id=user_id, group_id=group_id, session_id=session_id)
        scope_type, scope_id = target.scope.split("_", 1)
        rows = await conversation_data_port.get().router.sqlite.fetch_conversation_with_id(
            scope_type=scope_type, scope_id=scope_id, limit=limit, before_id=before_id, after_id=after_id,
        )
        return {"status": "ok", "scope": target.scope, "messages": rows}

    @router.get("/api/chat/stream", dependencies=portal_dependencies())
    async def chat_stream(request: Request, user_id: str = "", group_id: str = "", session_id: str = ""):
        """SSE 流端点：推送该 user 的 delta / tool_call / reply / turn_end 帧。"""
        from fastapi.responses import JSONResponse
        if not adapter._check_auth(request):
            return JSONResponse(
                {"status": "error", "error": "未认证：缺少或错误的 API Token"},
                status_code=401,
            )
        from sse_starlette.sse import EventSourceResponse

        if not user_id and not group_id:
            raise HTTPException(422, "必须提供 user_id 或 group_id")
        if len(adapter._stream_subs) >= adapter.config.max_stream_subscribers:
            raise HTTPException(503, "SSE 订阅数量已达上限")
        try:
            target = SessionTarget(user_id=user_id or "api_user", group_id=group_id, session_id=session_id)
        except ValueError as exc:
            raise HTTPException(422, "非法会话标识") from exc
        sub = _StreamSub(target.scope, include_children=not bool(session_id),
                         maxsize=adapter.config.stream_queue_size)
        adapter._stream_subs.append(sub)

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(sub.queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
                        continue
                    if not data:  # 频道关停唤醒信号
                        break
                    yield {"event": "message", "data": data}
            finally:
                if sub in adapter._stream_subs:
                    adapter._stream_subs.remove(sub)

        return EventSourceResponse(event_generator(), send_timeout=30.0)

    @router.post("/api/vision/push", dependencies=portal_dependencies())
    async def vision_push(req: VisionPushRequest, request: Request) -> Dict[str, Any]:
        """外部视觉源推送一帧（与主 web 端 /api/vision/push 同一缓冲入口）。"""
        if not adapter._check_auth(request):
            return {"status": "error", "error": "未认证：缺少或错误的 API Token"}
        from agent.vision.framework import push_external_frame
        try:
            result = await push_external_frame(
                req.source, req.image_base64, req.mime_type, req.captured_at)
        except (ValueError, OverflowError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", **result}

    @router.websocket("/api/vision/stream")
    async def vision_stream(websocket: WebSocket) -> None:
        """外部视觉源持续推流：每帧一条 JSON 文本，逐帧汇入统一缓冲并回 ack。

        帧格式同 POST /api/vision/push 的字段；坏帧只回错误帧不断连。
        实时画面的判变、缓冲与上下文注入由系统内部完成，推流方只负责采帧上行。
        """
        if not adapter._check_ws_auth(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        from agent.vision.framework import push_external_frame
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                raw = message.get("text")
                if raw is None:
                    # 二进制帧不支持：推流契约是 JSON 文本帧
                    await websocket.send_text(json.dumps(
                        {"status": "error", "error": "仅接受 JSON 文本帧"},
                        ensure_ascii=False))
                    continue
                try:
                    frame = json.loads(raw)
                    captured_at = frame.get("captured_at")
                    if not isinstance(captured_at, (int, float)):
                        captured_at = None
                    result = await push_external_frame(
                        str(frame.get("source", "")),
                        str(frame.get("image_base64", "")),
                        str(frame.get("mime_type", "image/png")),
                        captured_at,
                    )
                    ack: Dict[str, Any] = {
                        "status": "ok", "changed": result["changed"],
                        "source": result["source"],
                    }
                except (ValueError, OverflowError) as exc:
                    ack = {"status": "error", "error": str(exc)}
                except (json.JSONDecodeError, TypeError, AttributeError):
                    ack = {"status": "error", "error": "帧必须是 JSON 文本"}
                await websocket.send_text(json.dumps(ack, ensure_ascii=False))
        except WebSocketDisconnect:
            pass

    return router
