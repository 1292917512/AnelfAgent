"""QQ 传输层 — 正向/反向 WebSocket 连接管理与 OneBot API 调用（HTTP 优先，降级 WS）。"""

from __future__ import annotations

import asyncio
import hmac
import json
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp
from aiohttp import web

from agent.channel.channel_types import ChannelStatus
from agent.channel.schemas import ChannelType
from core.log import log

from .parser import parse_event_async

if TYPE_CHECKING:
    from .adapter import OneBotV11Channel


class QQTransport:
    """QQ 传输层：维护 WS 连接、事件分发与 API 请求/响应（echo）配对。"""

    def __init__(self, channel: "OneBotV11Channel") -> None:
        self._ch = channel

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """按配置启动反向 WS Server 或正向 WS 连接循环。"""
        ch = self._ch
        ch._session = aiohttp.ClientSession()

        mode = ch._cfg("ws_mode", "reverse")
        if mode == "reverse":
            await self._start_reverse_ws()
        else:
            ch._status = ChannelStatus.RECONNECTING
            ch._listen_task = asyncio.create_task(
                self._connect_loop(), name="qq_ws"
            )
            log(f"QQ: forward WS mode, connecting to {ch._cfg('ws_url', '?')} ...")

    async def stop(self) -> None:
        """停止连接循环、取消事件任务、失败化挂起 echo 并释放连接资源。"""
        ch = self._ch
        ch._status = ChannelStatus.STOPPED
        if ch._listen_task:
            ch._listen_task.cancel()
            try:
                await ch._listen_task
            except asyncio.CancelledError:
                pass  # 取消属正常关闭流程（正常控制流，非异常）
            ch._listen_task = None
        for task in ch._event_tasks:
            task.cancel()
        if ch._event_tasks:
            await asyncio.gather(*ch._event_tasks, return_exceptions=True)
            ch._event_tasks.clear()
        self._fail_pending_echoes("channel stopped")
        if ch._ws:
            try:
                await ch._ws.close()
            except OSError as exc:
                log(f"QQ: 关闭 WS 异常 -> {exc}", "DEBUG", tag="通道")
            ch._ws = None
        if ch._reverse_runner:
            try:
                await ch._reverse_runner.cleanup()
            except Exception as exc:
                log(f"QQ: 清理反向 WS runner 异常 -> {exc}", "DEBUG", tag="通道")
            ch._reverse_runner = None
        if ch._session and not ch._session.closed:
            try:
                await ch._session.close()
            except Exception as exc:
                log(f"QQ: 关闭 HTTP session 异常 -> {exc}", "DEBUG", tag="通道")
            ch._session = None

    # ------------------------------------------------------------------
    # 反向 WebSocket（OneBot 端连我们）
    # ------------------------------------------------------------------

    async def _start_reverse_ws(self) -> None:
        """启动反向 WS Server，等待 OneBot 端连接。"""
        ch = self._ch
        host = str(ch._cfg("reverse_ws_host", "127.0.0.1"))
        port = int(ch._cfg("reverse_ws_port", 8095))
        token = ch._cfg("access_token", "")

        if host not in ("127.0.0.1", "localhost", "::1") and not token:
            raise RuntimeError(
                f"QQ 反向 WS 监听非回环地址 {host} 但未配置 access_token，"
                "拒绝启动（任何主机都可注入伪造 OneBot 事件）。"
                "请配置 access_token 或将 reverse_ws_host 改为 127.0.0.1"
            )

        app = web.Application()
        app.router.add_get("/onebot/v11/ws", self._reverse_ws_handler)
        app.router.add_get("/onebot/v11/ws/", self._reverse_ws_handler)

        ch._reverse_runner = web.AppRunner(app)
        await ch._reverse_runner.setup()
        site = web.TCPSite(ch._reverse_runner, host, port)
        await site.start()
        ch._status = ChannelStatus.RUNNING
        log(f"QQ: 反向 WS Server 已启动，等待连接 ws://{host}:{port}/onebot/v11/ws"
            f"（认证: {'token' if token else '仅回环'}）")

    async def _reverse_ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """处理 OneBot 端的反向 WS 连接。"""
        ch = self._ch
        token = ch._cfg("access_token", "")
        if token:
            auth = request.headers.get("Authorization", "")
            provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            query_token = request.query.get("access_token", "")
            if not (hmac.compare_digest(provided, token) if provided else False) \
                    and not (hmac.compare_digest(query_token, token) if query_token else False):
                return web.Response(status=403, text="Forbidden")

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        if ch._ws:
            try:
                await ch._ws.close()
            except OSError as exc:
                log(f"QQ: 关闭旧 WS 连接异常 -> {exc}", "DEBUG", tag="通道")
            # 旧连接的在途 echo 等不到响应：立即失败；同时让旧 handler 的
            # finally 只处理"自己仍是当前连接"的断开，不误杀新连接的在途调用
            self._fail_pending_echoes("ws replaced by new connection")
        ch._ws = ws
        log("QQ: 客户端已连接（反向 WS）")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch_ws_data(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            if ch._ws is ws:
                ch._ws = None
                self._fail_pending_echoes("ws disconnected")
            log("QQ: 客户端断开连接（反向 WS）")

        return ws

    # ------------------------------------------------------------------
    # 正向 WebSocket（我们连 OneBot 端）
    # ------------------------------------------------------------------

    async def _connect_loop(self) -> None:
        """持续尝试连接 WebSocket，断线后自动重连。"""
        ch = self._ch
        reconnect_interval: int = ch._cfg("reconnect_interval", 5)
        max_attempts: int = ch._cfg("max_reconnect_attempts", 0)
        attempt = 0

        while ch._status != ChannelStatus.STOPPED:
            ws_url: str = ch._cfg("ws_url", "ws://127.0.0.1:3001")
            access_token: str = ch._cfg("access_token", "")

            headers: Dict[str, str] = {}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            try:
                log(f"QQ: 正在连接 {ws_url} ...")
                if ch._session is None:
                    raise RuntimeError("QQ: HTTP session 未初始化")
                ch._ws = await ch._session.ws_connect(
                    ws_url, headers=headers, heartbeat=30.0
                )
                ch._status = ChannelStatus.RUNNING
                attempt = 0
                log(f"QQ: WebSocket 已连接 ({ws_url})")

                await self._listen()

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"QQ: 连接异常 -> {exc}", "WARNING")

            if ch._status == ChannelStatus.STOPPED:
                break

            attempt += 1
            if max_attempts > 0 and attempt >= max_attempts:
                log("QQ: 达到最大重连次数，停止重连", "ERROR")
                ch._status = ChannelStatus.ERROR
                break

            ch._status = ChannelStatus.RECONNECTING
            log(f"QQ: {reconnect_interval}s 后重连 (第 {attempt} 次) ...")
            await asyncio.sleep(reconnect_interval)

    async def _listen(self) -> None:
        """监听 WebSocket 消息。"""
        ch = self._ch
        if ch._ws is None:
            raise RuntimeError("QQ: WebSocket 未连接")
        try:
            async for ws_msg in ch._ws:
                if ws_msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(ws_msg.data)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch_ws_data(data)
                elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            self._fail_pending_echoes("ws disconnected")

    # ------------------------------------------------------------------
    # 事件分发
    # ------------------------------------------------------------------

    def _dispatch_ws_data(self, data: Dict[str, Any]) -> None:
        """分发单条 WS 数据：echo 响应在读循环内即时处理，事件投递到独立任务。

        事件处理中可能经 WS 回呼 API（等待 echo），若在读循环内联 await
        会阻塞 echo 的接收形成死等，因此事件一律异步处理。
        """
        ch = self._ch
        echo = data.get("echo")
        if echo and echo in ch._pending_echoes:
            fut = ch._pending_echoes.pop(echo)
            if not fut.done():
                fut.set_result(data)
            return

        task = asyncio.create_task(self._process_event(data))
        ch._event_tasks.add(task)
        task.add_done_callback(ch._event_tasks.discard)

    async def _process_event(self, data: Dict[str, Any]) -> None:
        """异步处理单个 OneBot 事件。"""
        ch = self._ch
        try:
            self_id = data.get("self_id")
            if self_id:
                ch._self_id = str(self_id)

            if not ch._check_whitelist(data):
                log(f"QQ 白名单拦截: group={data.get('group_id')} user={data.get('user_id')} "
                    f"的消息已被丢弃（不在白名单内）", "DEBUG", tag="通道")
                return

            # 使用异步解析，支持获取引用消息内容、群成员昵称和合并转发
            message = await parse_event_async(data, self.call_api_raw)
            if message:
                # require_mention: 群聊中非 @ 消息仍记录到历史，但不触发思考
                if (
                    ch._cfg("require_mention", False)
                    and not message.is_to_me
                    and message.channel.channel_type == ChannelType.GROUP
                ):
                    message.trigger_mind = False
                await ch.on_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"QQ: 事件处理异常 -> {exc}", "ERROR")

    def _fail_pending_echoes(self, reason: str) -> None:
        """断线时将等待中的 echo Future 全部失败化，避免发送方挂到超时。"""
        ch = self._ch
        for fut in ch._pending_echoes.values():
            if not fut.done():
                fut.set_exception(ConnectionError(f"QQ WS 连接已断开: {reason}"))
        ch._pending_echoes.clear()

    # ------------------------------------------------------------------
    # OneBot API 调用（HTTP 优先，降级 WS）
    # ------------------------------------------------------------------

    async def call_api_data(self, action: str, params: Dict[str, Any]) -> Optional[Any]:
        """调用 API 并返回 data 字段，失败返回 None。"""
        result = await self.call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return result.get("data")
        return None

    async def call_api_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用 API 并返回完整响应（HTTP 优先，降级 WS）。"""
        http_url = self._ch._cfg("http_api_url", "")
        if http_url:
            return await self._call_api_http_raw(http_url, action, params)
        return await self._call_api_ws_raw(action, params)

    async def call_api(self, action: str, params: Dict[str, Any]) -> bool:
        """调用 OneBot v11 API（优先 HTTP，降级到 WS），返回成功与否。"""
        result = await self.call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return True
        if result:
            log(f"OneBot v11 API 失败: {action} -> {result}", "WARNING")
        return False

    async def _call_api_http_raw(
        self, base_url: str, action: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """通过 HTTP POST 调用 OneBot API，返回完整响应体。"""
        ch = self._ch
        url = f"{base_url.rstrip('/')}/{action}"
        access_token = ch._cfg("access_token", "")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            if ch._session is None:
                log("OneBot v11 HTTP API 调用失败: session 未初始化", "ERROR")
                return None
            async with ch._session.post(url, json=params, headers=headers) as resp:
                return await resp.json()
        except Exception as exc:
            log(f"OneBot v11 HTTP API 异常: {action} -> {exc}", "ERROR")
            return None

    async def _call_api_ws_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过 WebSocket 调用 OneBot API，返回完整响应体。"""
        ch = self._ch
        if not ch._ws or ch._ws.closed:
            log("QQ: WebSocket 未连接，无法发送", "WARNING")
            return None

        echo = uuid.uuid4().hex[:12]
        payload = {"action": action, "params": params, "echo": echo}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Dict[str, Any]] = loop.create_future()
        ch._pending_echoes[echo] = fut

        try:
            await ch._ws.send_json(payload)
            return await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            ch._pending_echoes.pop(echo, None)
            log(f"OneBot v11 WS API 超时: {action}", "WARNING")
            return None
        except Exception as exc:
            ch._pending_echoes.pop(echo, None)
            log(f"OneBot v11 WS API 异常: {action} -> {exc}", "ERROR")
            return None
