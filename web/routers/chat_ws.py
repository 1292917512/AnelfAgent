"""聊天 WebSocket 端点 — 双向实时通道（桌面壳/新客户端的统一入口）。

协议（envelope）：
- 客户端→服务端：文本帧 JSON ``{"action": ..., "request_id"?: ...}``；
  二进制帧仅承载麦克风 PCM（帧头格式见 core/audio_frames）；
- 服务端→客户端：``{"type": ..., ...}`` 数据帧（事件名与 SSE 帧一致，
  两种传输共享 realtime_hub 同一事件面）+ ``{"type": "status",
  "message": {"code", "details"}}`` 结构化错误帧；
- request_id 关联：每个请求类 action 的 ack/status 帧回带请求方 id
  （多窗口下 ack 到达的窗口不一定是请求方）；
- 连接仲裁：client=desktop 单槽位"最新连接赢"（重连/多开防双写），
  失势连接收 CONNECTION_SUPERSEDED 状态帧后关闭；client=web 多开不踢。

action 清单（v1）：ping / send_message / interrupt / ui_answer /
ui_state_report / voice_start / voice_end；音频会话别名
start_session(input_type=audio) / end_session。

鉴权：BaseHTTPMiddleware 不覆盖 WS，本端点自校验——与 HTTP 面同一
token 体系（cookie _anelf_token 或 ?token= 查询参数）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core import realtime_hub
from core.audio_frames import AudioFrameError, decode_audio_frame
from core.log import log

router = APIRouter(prefix="/chat", tags=["chat-ws"])

_LOG_TAG = "ChatWS"

# 结构化错误码（封闭集，前端分支依据；新增码需同步文档）
ERR_UNAUTHORIZED = "UNAUTHORIZED"
ERR_UNKNOWN_ACTION = "UNKNOWN_ACTION"
ERR_INVALID_PAYLOAD = "INVALID_PAYLOAD"
ERR_BAD_AUDIO_FRAME = "BAD_AUDIO_FRAME"
ERR_VOICE_SESSION_BUSY = "VOICE_SESSION_BUSY"
ERR_CONNECTION_SUPERSEDED = "CONNECTION_SUPERSEDED"
ERR_RUNTIME_NOT_READY = "RUNTIME_NOT_READY"
ERR_SERVER_ERROR = "SERVER_ERROR"

# client=desktop 的单槽位连接（kind → (connection_id, websocket)）
_desktop_slot: Dict[str, Any] = {}


def _status(code: str, details: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    frame: Dict[str, Any] = {"type": "status", "message": {"code": code, "details": details}}
    if request_id:
        frame["request_id"] = request_id
    return frame


def _check_auth(websocket: WebSocket) -> bool:
    """与 HTTP 面同一密码体系：cookie 或 ?token= 任一命中即放行（未设密码全放行）。"""
    from web.server import _load_auth_password, _make_token
    password = _load_auth_password()
    if not password:
        return True
    token = websocket.query_params.get("token") or websocket.cookies.get("_anelf_token", "")
    return token == _make_token(password)


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    if not _check_auth(websocket):
        await websocket.close(code=4401)
        return

    client_kind = websocket.query_params.get("client", "web")
    if client_kind not in ("web", "desktop"):
        client_kind = "web"
    user_id = websocket.query_params.get("user_id", "web_user")
    user_name = websocket.query_params.get("user_name", "用户")
    chat_id = websocket.query_params.get("chat_id", "")

    await websocket.accept()
    sub = realtime_hub.subscribe(client_kind=client_kind)
    conn_id = sub.connection_id

    # desktop 单槽位仲裁：新连接顶掉旧连接（防重连/多开双写）
    if client_kind == "desktop":
        old = _desktop_slot.get("desktop")
        if old is not None and old[0] != conn_id:
            try:
                await old[1].send_json(_status(
                    ERR_CONNECTION_SUPERSEDED, "新的桌面客户端已接管连接",
                ))
                await old[1].close(code=4000)
            except Exception:
                pass
        _desktop_slot["desktop"] = (conn_id, websocket)

    downstream = asyncio.create_task(
        _pump_downstream(websocket, sub), name=f"chat_ws.down.{conn_id}",
    )
    try:
        await _pump_upstream(websocket, conn_id, sub, user_id, user_name, chat_id)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log(f"WS 上行泵异常: {exc}", "DEBUG", tag=_LOG_TAG)
    finally:
        downstream.cancel()
        realtime_hub.unsubscribe(sub)
        if _desktop_slot.get("desktop", (None, None))[0] == conn_id:
            _desktop_slot.pop("desktop", None)
        from services.voice import get_voice_service
        await get_voice_service().drop_connection(conn_id)


async def _pump_downstream(websocket: WebSocket, sub: realtime_hub.Subscriber) -> None:
    """枢纽事件 → WS 数据帧（{"type": 事件名, ...}；事件面与 SSE 一致）。

    dead 判定带超时轮询：判死后不再有新帧入队，阻塞在 queue.get() 上
    会永远等不到——30s 无帧即复查 dead/连接活性。
    """
    try:
        while True:
            if sub.dead:
                await websocket.send_json(_status(
                    ERR_SERVER_ERROR, "事件积压超限，连接重置（请重连后重同步）",
                ))
                await websocket.close(code=1013)
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                continue  # 回到循环头复查 dead/连接状态
            # 下行音频帧（实时语音）：二进制 PCM 帧直发（audio_chunk 数据面）
            if event.get("__audio__"):
                from core.audio_frames import encode_audio_frame
                await websocket.send_bytes(
                    encode_audio_frame(event["pcm"], int(event["rate"])))
                continue
            payload = {k: v for k, v in event.items() if k != "event"}
            await websocket.send_json({"type": event.get("event", "message"), **payload})
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return
    except Exception as exc:
        log(f"WS 下行泵异常: {exc}", "DEBUG", tag=_LOG_TAG)


async def _pump_upstream(
    websocket: WebSocket,
    conn_id: str,
    sub: realtime_hub.Subscriber,
    user_id: str,
    user_name: str,
    chat_id: str,
) -> None:
    """WS 帧 → action 分发（文本帧 JSON 控制面 / 二进制帧音频数据面）。"""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if message.get("bytes") is not None:
            await _handle_binary(websocket, conn_id, message["bytes"])
            continue
        text = message.get("text")
        if text is None:
            continue
        try:
            frame = json.loads(text)
        except ValueError:
            await websocket.send_json(_status(ERR_INVALID_PAYLOAD, "文本帧必须是 JSON"))
            continue
        if not isinstance(frame, dict):
            await websocket.send_json(_status(ERR_INVALID_PAYLOAD, "帧必须是 JSON 对象"))
            continue
        await _dispatch(websocket, conn_id, sub, frame, user_id, user_name, chat_id)


async def _handle_binary(websocket: WebSocket, conn_id: str, data: bytes) -> None:
    """二进制帧 = 音频数据面（PCM 帧）；坏帧只丢帧不关连接。"""
    try:
        frame = decode_audio_frame(data)
    except AudioFrameError as exc:
        log(f"坏音频帧: {exc}", "DEBUG", tag=_LOG_TAG)
        return
    from services.voice import get_voice_service
    await get_voice_service().accept_frame(conn_id, frame)


async def _dispatch(
    websocket: WebSocket,
    conn_id: str,
    sub: realtime_hub.Subscriber,
    frame: Dict[str, Any],
    user_id: str,
    user_name: str,
    chat_id: str,
) -> None:
    """action 分发；请求类 action 的响应帧一律回带 request_id。"""
    action = str(frame.get("action", ""))
    request_id = frame.get("request_id")

    async def ack(frame_type: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {"type": frame_type, **fields}
        if request_id:
            payload["request_id"] = request_id
        await websocket.send_json(payload)

    try:
        if action == "ping":
            await ack("pong")

        elif action == "send_message":
            message = str(frame.get("message", "") or "")
            images = frame.get("images")
            files = frame.get("files")
            if not message and not images and not files:
                await websocket.send_json(_status(
                    ERR_INVALID_PAYLOAD, "message/images/files 至少一项非空", request_id,
                ))
                return
            from services import ChatService
            await ChatService().send_web_message(
                message,
                images=images if isinstance(images, list) else None,
                files=files if isinstance(files, list) else None,
                user_id=str(frame.get("user_id") or user_id),
                user_name=str(frame.get("user_name") or user_name),
                chat_id=str(frame.get("chat_id") or chat_id) or None,
            )
            await ack("send_ack", ok=True)

        elif action == "interrupt":
            from services import ChatService
            result = ChatService().interrupt_chat(str(frame.get("chat_id") or chat_id or "default"))
            await ack("interrupt_ack", **result)

        elif action == "ui_answer":
            from services import UiService
            ok = UiService().resolve_ask(
                str(frame.get("ask_id", "")), str(frame.get("answer", "")),
            )
            await ack("ui_answer_ack", ok=ok)

        elif action == "ui_state_report":
            state = frame.get("state")
            if not isinstance(state, dict):
                await websocket.send_json(_status(ERR_INVALID_PAYLOAD, "state 必须是对象", request_id))
                return
            from services import UiService
            UiService().update_ui_state(state)
            await ack("ui_state_ack", ok=True)

        elif action in ("voice_start", "start_session"):
            # 别名 action：start_session 仅接受 audio 会话（其余 input_type 显式拒绝）
            input_type = str(frame.get("input_type", "audio"))
            if action == "start_session" and input_type not in ("audio", ""):
                await websocket.send_json(_status(
                    ERR_INVALID_PAYLOAD, f"暂不支持的 input_type: {input_type}", request_id,
                ))
                return
            from services.voice import VoiceLeaseBusy, get_voice_service
            try:
                if str(frame.get("mode", "")) == "realtime":
                    # 实时对话：sink 直接挂在本连接的订阅队列上（JSON 事件走
                    # 下行泵，音频帧以 __audio__ 标记转二进制帧）
                    from services.voice import RealtimeSink

                    async def _send_audio(pcm: bytes, rate: int) -> None:
                        while True:
                            try:
                                sub.queue.put_nowait(
                                    {"__audio__": True, "pcm": pcm, "rate": rate})
                                return
                            except asyncio.QueueFull:
                                try:
                                    sub.queue.get_nowait()  # 丢最旧帧保最新
                                except asyncio.QueueEmpty:
                                    pass

                    async def _send_event(name: str, payload: Dict[str, Any]) -> None:
                        try:
                            sub.queue.put_nowait({"event": name, **payload})
                        except asyncio.QueueFull:
                            pass

                    await get_voice_service().start_realtime(
                        conn_id,
                        sink=RealtimeSink(
                            send_audio=_send_audio, send_event=_send_event),
                        sample_rate=int(frame.get("sample_rate", 16000)),
                        user_id=str(frame.get("user_id") or user_id),
                        user_name=str(frame.get("user_name") or user_name),
                        chat_id=str(frame.get("chat_id") or chat_id),
                    )
                else:
                    await get_voice_service().start(
                        conn_id,
                        sample_rate=int(frame.get("sample_rate", 48000)),
                        user_id=str(frame.get("user_id") or user_id),
                        user_name=str(frame.get("user_name") or user_name),
                        chat_id=str(frame.get("chat_id") or chat_id),
                    )
            except VoiceLeaseBusy as exc:
                await websocket.send_json(_status(ERR_VOICE_SESSION_BUSY, str(exc), request_id))
                return
            await ack("voice_ack", ok=True, active=True)

        elif action in ("voice_end", "end_session"):
            from services.voice import get_voice_service
            await get_voice_service().end(conn_id)
            await ack("voice_ack", ok=True, active=False)

        else:
            await websocket.send_json(_status(ERR_UNKNOWN_ACTION, f"未知 action: {action}", request_id))
    except Exception as exc:
        log(f"action 处理异常: {action} - {exc}", "DEBUG", tag=_LOG_TAG)
        await websocket.send_json(_status(ERR_SERVER_ERROR, str(exc), request_id))
