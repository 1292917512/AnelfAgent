"""AcFun 直播间连接 — 单房间 klink websocket 生命周期。

状态机（对齐看门狗契约：RECONNECTING 为自愈中，不打扰守护）：

    DISCONNECTED → CONNECTING → REGISTERED → CONNECTED（已进房，收发弹幕）
        ↑                |             |            |
        └── 网络异常退避重连 ┘             └─ 换票重进 ┘└─ LIVE_CLOSED/BANNED → CLOSED（慢速重探）

心跳：进房 ack 返回 heartbeatIntervalMs（默认 10s，钳制 3~60s），周期发送
ZtLiveCsHeartbeat（每 5 拍附一次 Basic.KeepAlive）；连续 miss 2 个周期无任何
下行（心跳 ack / push 均算活跃）判连接死亡强制重连。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from core.log import log

from . import signals as live_signals
from .protocol import (
    DownstreamPayload,
    KlinkCodec,
    ProtocolError,
    decode_sc_message,
    enter_room_body,
    heartbeat_body,
    parse_cs_cmd_ack,
    parse_enter_room_ack,
    parse_register_response,
    user_exit_body,
)

WS_ENDPOINTS = (
    "wss://klink-newproduct-ws2.kwaizt.com",
    "wss://klink-newproduct-ws3.kwaizt.com",
    "wss://klink-newproduct-ws2.kuaishouzt.com",
    "wss://klink-newproduct-ws3.kuaishouzt.com",
)

_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 120.0
_HEARTBEAT_DEFAULT_MS = 10_000
_HEARTBEAT_MIN_MS = 3_000
_HEARTBEAT_MAX_MS = 60_000


class RoomState(str, Enum):
    """直播间连接状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"          # 主播未开播/已下播，慢速重探
    STOPPED = "stopped"        # 已解除观察或频道停止


@dataclass
class EnterParams:
    """进房参数（startPlay HTTP 接口获得）。"""

    live_id: str = ""
    tickets: List[str] = field(default_factory=list)
    enter_room_attach: str = ""
    is_author: bool = False
    title: str = ""
    user_name: str = ""


@dataclass
class RoomStats:
    """连接诊断计数（acfun_live_status 工具与上下文注入的数据源）。"""

    danmaku: int = 0
    likes: int = 0
    enters: int = 0
    follows: int = 0
    gifts: int = 0
    unknown_signals: int = 0
    reconnects: int = 0
    ticket_rotations: int = 0
    param_refreshes: int = 0
    last_error: str = ""
    last_signal_at: float = 0.0
    connected_since: float = 0.0
    watching: str = ""
    like_total: str = ""
    banana_total: str = ""


EnterParamsFn = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]
CredentialsFn = Callable[[], Optional[Dict[str, Any]]]
EventCallback = Callable[[str, "live_signals.LiveEvent"], Awaitable[None]]
StateCallback = Callable[[str, RoomState, str], Awaitable[None]]


class LiveRoomConnection:
    """单个直播间的 klink 连接（独立后台任务，由 LiveSessionManager 驱动）。"""

    def __init__(
        self,
        uid: str,
        *,
        enter_params_fn: EnterParamsFn,
        credentials_fn: CredentialsFn,
        on_event: EventCallback,
        on_state: StateCallback,
        closed_retry_seconds: float = 300.0,
    ) -> None:
        self.uid = str(uid)
        self._enter_params_fn = enter_params_fn
        self._credentials_fn = credentials_fn
        self._event_cb = on_event
        self._state_cb = on_state
        self._closed_retry = max(closed_retry_seconds, 30.0)
        self.state = RoomState.DISCONNECTED
        self.state_detail = ""
        self.stats = RoomStats()
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[Any] = None
        self._codec: Optional[KlinkCodec] = None
        self._params = EnterParams()
        self._ticket_index = 0
        self._endpoint_index = 0
        self._heartbeat_interval = _HEARTBEAT_DEFAULT_MS / 1000
        self._heartbeat_seq = 0
        self._last_inbound_at = 0.0
        self._last_heartbeat_sent_at = 0.0
        self._registered = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name=f"acfun-live-{self.uid}")

    async def stop(self) -> None:
        """停止连接（离开房间 + 关闭 websocket，幂等）。"""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._set_state(RoomState.STOPPED, "已停止")

    async def refresh_params_now(self) -> None:
        """外部要求立即重连（如 NEW_LIVE_OPENED 后）：标记参数失效，踢醒连接循环。"""
        self._params = EnterParams()
        if self._ws is not None and not self._ws.closed:
            await self._safe_ws_close()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        failures = 0
        while True:
            try:
                connected = await self._connect_and_enter()
                if connected:
                    failures = 0
                    await self._listen()
                # _listen 正常返回 = 连接断开（主播关播/服务端踢出已处理）
            except asyncio.CancelledError:
                await self._teardown()
                raise
            except Exception as exc:
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                log(f"AcFun直播: 房间 {self.uid} 连接异常: {exc}", "WARNING", tag="通道")

            if self.state == RoomState.CLOSED:
                # 主播未开播：慢速重探，不计失败
                await asyncio.sleep(self._closed_retry)
                continue

            failures += 1
            self.stats.reconnects += 1
            backoff = min(_BACKOFF_BASE * (2 ** min(failures - 1, 6)), _BACKOFF_CAP)
            self._set_state(RoomState.RECONNECTING, f"第 {failures} 次重连，{backoff:.0f}s 后")
            await self._notify_state()
            await asyncio.sleep(backoff)

    async def _connect_and_enter(self) -> bool:
        """取进房参数 → 连 ws → 注册 → 进房。返回是否成功进房。"""
        self._set_state(RoomState.CONNECTING, "获取进房参数")
        params = await self._enter_params_fn(self.uid)
        if not params or not params.get("is_open"):
            title = str((params or {}).get("title") or "")
            self._set_state(RoomState.CLOSED, f"主播未开播 {title}".strip())
            await self._notify_state()
            return False
        self._params = EnterParams(
            live_id=str(params["live_id"]),
            tickets=[str(t) for t in params.get("tickets") or []],
            enter_room_attach=str(params.get("enter_room_attach") or ""),
            is_author=bool(params.get("is_author")),
            title=str(params.get("title") or ""),
            user_name=str(params.get("user_name") or ""),
        )
        self._ticket_index = 0

        credentials = self._credentials_fn()
        if not credentials:
            raise RuntimeError("AcFun 登录态不可用，无法建立直播连接")
        self._codec = KlinkCodec(
            uid=int(credentials["uid"]),
            did=str(credentials["did"]),
            ssecurity=str(credentials["ssecurity"]),
            service_token=str(credentials["token"]),
        )
        self._registered = False

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        endpoint = WS_ENDPOINTS[self._endpoint_index % len(WS_ENDPOINTS)]
        self._ws = await self._session.ws_connect(
            endpoint, origin="https://live.acfun.cn", heartbeat=10.0,
        )
        self._endpoint_index += 1

        await self._send(self._codec.register())
        await self._send(self._codec.client_config_get())
        await self._send(self._codec.keepalive())
        await self._send(self._codec.cs_cmd(
            "ZtLiveCsEnterRoom",
            enter_room_body(self._params.enter_room_attach, self._params.is_author),
            self._params.live_id, self._current_ticket,
        ))
        self._set_state(RoomState.CONNECTING, "等待进房确认")
        return True

    async def _listen(self) -> None:
        """读循环：处理下行帧；进房 ack 到达后才算 CONNECTED。"""
        ws = self._ws
        if ws is None:
            raise ConnectionError("直播 websocket 未建立")
        entered = False
        self._last_inbound_at = time.time()
        while True:
            in_room = entered and self._codec is not None and self._codec.has_session
            timeout = self._heartbeat_interval if in_room else 30.0
            try:
                msg = await ws.receive(timeout=timeout + 1.0)
            except asyncio.TimeoutError:
                if in_room and self._is_stale():
                    log(f"AcFun直播: 房间 {self.uid} 心跳超时判死，重连", "WARNING", tag="通道")
                    return
                if in_room:
                    await self._send_heartbeat()
                continue
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                return
            if msg.type != aiohttp.WSMsgType.BINARY or msg.data is None:
                continue
            self._last_inbound_at = time.time()
            codec = self._codec
            if codec is None:
                continue
            try:
                down = codec.decode(bytes(msg.data))
            except ProtocolError as exc:
                self.stats.last_error = str(exc)
                log(f"AcFun直播: 房间 {self.uid} 下行帧解析失败: {exc}", "DEBUG", tag="通道")
                continue
            if down.is_error and down.command != "Push.ZtLiveInteractive.Message":
                self.stats.last_error = f"{down.command}: {down.error_msg or down.error_code}"
                log(f"AcFun直播: 房间 {self.uid} 下行错误 {down.command}: "
                    f"{down.error_msg or down.error_code}", "WARNING", tag="通道")
            entered = await self._handle_downlink(down, entered) or entered

    # ------------------------------------------------------------------
    # 下行分发
    # ------------------------------------------------------------------

    async def _handle_downlink(self, down: DownstreamPayload, entered: bool) -> bool:
        command = down.command
        if command == "Basic.RegisterAck":
            sess_key, instance_id = parse_register_response(down.payload_data)
            if self._codec is not None and down.header is not None:
                self._codec.adopt_session(sess_key, instance_id, down.header.app_id)
            self._registered = True
            return entered
        if command == "Global.ZtLiveInteractive.CsCmdAck":
            return await self._handle_cs_ack(down.payload_data, entered)
        if command == "Push.ZtLiveInteractive.Message":
            if self._codec is not None:
                await self._send(self._codec.push_reply())
            await self._handle_sc_push(down.payload_data)
            return entered
        # KeepAlive / Ping / ClientConfigGet 等 ack：保活流量，无需处理
        return entered

    async def _handle_cs_ack(self, payload: bytes, entered: bool) -> bool:
        ack_type, error_code, error_msg, ack_payload = parse_cs_cmd_ack(payload)
        if ack_type == "ZtLiveCsEnterRoomAck":
            if error_code:
                log(f"AcFun直播: 房间 {self.uid} 进房失败 code={error_code} {error_msg}",
                    "WARNING", tag="通道")
                self.stats.last_error = f"进房失败 code={error_code}"
                return entered
            self._heartbeat_interval = min(
                max(parse_enter_room_ack(ack_payload) / 1000 or _HEARTBEAT_DEFAULT_MS / 1000,
                    _HEARTBEAT_MIN_MS / 1000),
                _HEARTBEAT_MAX_MS / 1000,
            )
            self._heartbeat_seq = 0
            self._set_state(RoomState.CONNECTED, self._params.title)
            self.stats.connected_since = time.time()
            await self._notify_state()
            await self._send_heartbeat()
            return True
        if ack_type == "ZtLiveCsHeartbeatAck":
            self._last_inbound_at = time.time()
            return entered
        return entered

    async def _handle_sc_push(self, payload: bytes) -> None:
        try:
            sc_signals = decode_sc_message(payload)
        except ProtocolError as exc:
            self.stats.last_error = str(exc)
            return
        for sc in sc_signals:
            if sc.message_type == "ZtLiveScTicketInvalid":
                await self._rotate_ticket()
                continue
            for item_payload in sc.payloads:
                event = live_signals.interpret(sc.signal_type, item_payload)
                if event is None:
                    self.stats.unknown_signals += 1
                    continue
                await self._absorb_event(event)

    async def _absorb_event(self, event: "live_signals.LiveEvent") -> None:
        """事件落计数 + 上抛管理器。"""
        self.stats.last_signal_at = time.time()
        kind = event.kind
        if kind == live_signals.EVT_COMMENT:
            self.stats.danmaku += 1
        elif kind == live_signals.EVT_LIKE:
            self.stats.likes += 1
        elif kind == live_signals.EVT_ENTER:
            self.stats.enters += 1
        elif kind == live_signals.EVT_FOLLOW:
            self.stats.follows += 1
        elif kind in (live_signals.EVT_GIFT, live_signals.EVT_BANANA):
            self.stats.gifts += 1
        elif kind == live_signals.EVT_DISPLAY:
            if event.extra.get("watching"):
                self.stats.watching = str(event.extra["watching"])
            if event.extra.get("likes"):
                self.stats.like_total = str(event.extra["likes"])
            if event.extra.get("banana"):
                self.stats.banana_total = str(event.extra["banana"])
        elif kind == live_signals.EVT_STATUS_CHANGED:
            change = event.extra.get("change")
            if change in ("live_closed", "live_banned"):
                await self._event_cb(self.uid, event)
                self._set_state(RoomState.CLOSED, "主播已下播" if change == "live_closed"
                                else f"直播被封禁: {event.extra.get('reason', '')}")
                await self._notify_state()
                await self._safe_ws_close()
                return
            if change in ("new_live_opened", "live_url_changed"):
                await self._event_cb(self.uid, event)
                self.stats.param_refreshes += 1
                await self.refresh_params_now()  # 立即断开，主循环取新参数重连
                return
        await self._event_cb(self.uid, event)

    async def _rotate_ticket(self) -> None:
        """进房票失效：换下一张票重进（不重建 ws）。"""
        self.stats.ticket_rotations += 1
        if self._ticket_index + 1 < len(self._params.tickets):
            self._ticket_index += 1
        if self._codec is not None:
            await self._send(self._codec.cs_cmd(
                "ZtLiveCsEnterRoom",
                enter_room_body(self._params.enter_room_attach, self._params.is_author,
                                reconnect_count=1),
                self._params.live_id, self._current_ticket,
            ))
            log(f"AcFun直播: 房间 {self.uid} 已换票重进 (ticket#{self._ticket_index})",
                "DEBUG", tag="通道")
        else:
            await self._safe_ws_close()

    # ------------------------------------------------------------------
    # 心跳与工具
    # ------------------------------------------------------------------

    @property
    def _current_ticket(self) -> str:
        tickets = self._params.tickets
        return tickets[self._ticket_index] if tickets else ""

    def _is_stale(self) -> bool:
        """超过 2 个心跳周期无任何下行视为连接死亡。"""
        return time.time() - self._last_inbound_at > self._heartbeat_interval * 2

    async def _send_heartbeat(self) -> None:
        if self._codec is None:
            return
        now = time.time()
        if now - self._last_heartbeat_sent_at < self._heartbeat_interval - 1.0:
            return
        self._last_heartbeat_sent_at = now
        self._heartbeat_seq += 1
        await self._send(self._codec.cs_cmd(
            "ZtLiveCsHeartbeat", heartbeat_body(int(now * 1000), self._heartbeat_seq),
            self._params.live_id, self._current_ticket,
        ))
        if self._heartbeat_seq % 5 == 0:
            await self._send(self._codec.keepalive())

    async def _send(self, frame: bytes) -> None:
        if self._ws is None or self._ws.closed:
            raise ConnectionError("直播 websocket 已关闭")
        await self._ws.send_bytes(frame)

    async def _safe_ws_close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            try:
                if self._registered and self._codec is not None:
                    await self._send(self._codec.cs_cmd(
                        "ZtLiveCsUserExit", user_exit_body(),
                        self._params.live_id, self._current_ticket,
                    ))
                    await self._send(self._codec.unregister())
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _teardown(self) -> None:
        await self._safe_ws_close()
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None
        self._ws = None

    def _set_state(self, state: RoomState, detail: str = "") -> None:
        self.state = state
        self.state_detail = detail

    async def _notify_state(self) -> None:
        await self._state_cb(self.uid, self.state, self.state_detail)
