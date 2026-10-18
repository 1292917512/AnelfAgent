"""wcauto 传输层 — 正向 WebSocket 客户端：握手（token+seq 续传）、RPC 配对、事件分发。

断线重连指数退避（上限 60s）；每次重连用持久化的 last_seq 向桥要回补
（桥环形缓冲覆盖不了时以桥水位为准继续，缺口可经 get_messages 补拉）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional

import aiohttp

from agent.channel.channel_types import ChannelStatus
from core.log import log

from .bridge.wcauto_bridge import protocol

if TYPE_CHECKING:
    from .adapter import WcautoChannel


class WcautoRpcError(Exception):
    """桥 RPC 失败（业务错误透传 / 超时 / 断线）。"""


EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class WcautoTransport:
    """wcauto 频道传输层：维护到 Windows 桥的 WS 连接与 RPC 调用。"""

    def __init__(self, channel: "WcautoChannel") -> None:
        self._ch = channel
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._last_hello_at: float = 0.0
        self.self_info: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动连接循环（非阻塞，连接受挫后台退避重试）。"""
        ch = self._ch
        if self._connect_task and not self._connect_task.done():
            return
        ch._status = ChannelStatus.RECONNECTING
        self._session = aiohttp.ClientSession()
        self._connect_task = asyncio.create_task(self._connect_loop(), name="wcauto_ws")

    async def stop(self) -> None:
        """停止连接循环、失败化挂起 RPC 并释放连接。"""
        ch = self._ch
        ch._status = ChannelStatus.STOPPED
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            self._connect_task = None
        self._fail_pending("channel stopped")
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:
                log(f"wcauto: 关闭 WS 异常 -> {exc}", "DEBUG", tag="通道")
            self._ws = None
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as exc:
                log(f"wcauto: 关闭 HTTP session 异常 -> {exc}", "DEBUG", tag="通道")
            self._session = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    # ------------------------------------------------------------------
    # 连接循环
    # ------------------------------------------------------------------

    async def _connect_loop(self) -> None:
        """持续连接桥，断线自动重连（配置间隔，连续失败指数退避到 60s）。"""
        ch = self._ch
        interval = int(ch._cfg("reconnect_interval", 5))
        max_attempts = int(ch._cfg("max_reconnect_attempts", 0))
        attempt = 0
        backoff = float(interval)

        while ch._status != ChannelStatus.STOPPED:
            ws_url = str(ch._cfg("ws_url", ""))
            if not ws_url:
                log("wcauto: 未配置 ws_url，频道保持待连接", "WARNING", tag="通道")
                await asyncio.sleep(30)
                continue
            try:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession()
                log(f"wcauto: 正在连接桥 {ws_url} ...")
                token = str(ch._cfg("access_token", ""))
                ws_headers = {"Authorization": f"Bearer {token}"} if token else None
                async with self._session.ws_connect(
                    ws_url, heartbeat=30.0, max_msg_size=64 * 1024 * 1024,
                    headers=ws_headers,
                ) as ws:
                    if not await self._handshake(ws):
                        await asyncio.sleep(backoff)
                        continue
                    attempt = 0
                    backoff = float(interval)
                    self._ws = ws
                    ch._status = ChannelStatus.RUNNING
                    await self._recv_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"wcauto: 桥连接失败/断开 -> {exc}", "WARNING", tag="通道")
            finally:
                if self._ws is not None:
                    self._ws = None
                self._fail_pending("ws disconnected")
                if ch._status != ChannelStatus.STOPPED:
                    ch._status = ChannelStatus.RECONNECTING

            attempt += 1
            if max_attempts and attempt >= max_attempts:
                log(f"wcauto: 重连次数耗尽（{max_attempts}），频道置为 ERROR",
                    "WARNING", tag="通道")
                ch._status = ChannelStatus.ERROR
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)

    async def _handshake(self, ws: aiohttp.ClientWebSocketResponse) -> bool:
        """hello 握手：token 鉴权 + last_seq 续传；成功返回 True。"""
        ch = self._ch
        token = str(ch._cfg("access_token", ""))
        last_seq = ch._state_store.load_seq()
        await ws.send_str(json.dumps(protocol.make_hello(token, last_seq)))
        try:
            raw = await asyncio.wait_for(ws.receive_str(), timeout=20.0)
            resp = json.loads(raw)
        except Exception as exc:
            log(f"wcauto: 桥握手失败 -> {exc}", "WARNING", tag="通道")
            return False
        if resp.get("type") == protocol.TYPE_HELLO_ERR:
            log(f"wcauto: 桥拒绝连接: {resp.get('error')}"
                "（检查 access_token 是否与桥一致）", "WARNING", tag="通道")
            await asyncio.sleep(30)  # 鉴权类失败不做高频重试
            return False
        if resp.get("type") != protocol.TYPE_HELLO_OK:
            return False
        self.self_info = dict(resp.get("self_info") or {})
        self._last_hello_at = time.time()
        replayed = int(resp.get("replayed") or 0)
        log(f"wcauto: 已连接桥 account={self.self_info.get('username', '?')}"
            f" nickname={self.self_info.get('nick_name', '?')}"
            + (f"，回补 {replayed} 条离线事件" if replayed else ""), tag="通道")
        return True

    async def _recv_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """接收循环：事件分发 + RPC 结果配对。"""
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type")
                if msg_type == protocol.TYPE_RPC_RESULT:
                    self._resolve_rpc(data)
                elif msg_type == protocol.TYPE_EVENT:
                    seq = int(data.get("seq") or 0)
                    if seq:
                        self._ch._state_store.save_seq(seq)
                    asyncio.create_task(
                        self._dispatch_event(data),
                        name=f"wcauto_event_{seq}",
                    )
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _dispatch_event(self, data: Dict[str, Any]) -> None:
        try:
            await self._ch._on_bridge_event(data)
        except Exception as exc:
            log(f"wcauto: 事件处理异常 -> {exc}", "WARNING", tag="通道")

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------

    async def call_rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
                       timeout: Optional[float] = None) -> Any:
        """调用桥 RPC，成功返回 data；失败抛 WcautoRpcError。"""
        ws = self._ws
        if ws is None or ws.closed:
            raise WcautoRpcError("桥未连接（Windows 桥离线或重连中）")
        req = protocol.make_rpc(method, params)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req["id"]] = fut
        try:
            await ws.send_str(json.dumps(req, ensure_ascii=False))
            effective_timeout = timeout or float(self._ch._cfg("rpc_timeout", 90.0))
            resp = await asyncio.wait_for(fut, timeout=effective_timeout)
        except asyncio.TimeoutError:
            raise WcautoRpcError(f"桥 RPC 超时（{method}）") from None
        finally:
            self._pending.pop(req["id"], None)
        if not resp.get("ok"):
            raise WcautoRpcError(str(resp.get("error") or "桥调用失败"))
        return resp.get("data")

    def _resolve_rpc(self, data: Dict[str, Any]) -> None:
        fut = self._pending.get(str(data.get("id") or ""))
        if fut is not None and not fut.done():
            fut.set_result(data)

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(WcautoRpcError(reason))
        self._pending.clear()
