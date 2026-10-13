"""桥服务端 — aiohttp WebSocket 服务：鉴权握手、事件推送（断线重放）、RPC 分发。

单客户端策略：新连接顶替旧连接（同一时刻只有一个频道消费事件）。
所有 RPC 在默认 executor 线程执行（wechatauto 为同步阻塞 API），
事件经 asyncio.Queue 从 Listener 工作线程安全汇入发送循环。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from typing import Any, Dict, Optional, Set

import aiohttp
from aiohttp import web

from . import protocol
from .backend import BridgeError, WeChatBackend
from .config import BridgeConfig

WS_PATH = "/wcauto/ws"
HEALTH_PATH = "/wcauto/health"


class WcautoBridgeServer:
    """wcauto 桥服务（aiohttp WS）。"""

    def __init__(self, config: BridgeConfig,
                 backend: Optional[WeChatBackend] = None) -> None:
        self._cfg = config
        self._buffer = protocol.EventBuffer(config.event_buffer)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._backend = backend or WeChatBackend(config, self.emit_event)
        self._ws: Optional[web.WebSocketResponse] = None
        self._runner: Optional[web.AppRunner] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._rpc_tasks: Set[asyncio.Task] = set()
        self._started_at = time.time()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # 事件汇入（Listener 工作线程调用，线程安全）
    # ------------------------------------------------------------------

    def emit_event(self, event: str, data: Dict[str, Any]) -> None:
        """桥后端事件入口：跨线程投进 asyncio 队列（满则丢最旧，保链路不堵）。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, event, data)
        except RuntimeError:
            pass

    def _enqueue(self, event: str, data: Dict[str, Any]) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait((event, data))

    async def _pump(self) -> None:
        """事件泵：出队 → 分配 seq 入缓冲 → 推给当前客户端。"""
        while True:
            event, data = await self._queue.get()
            item = self._buffer.append(event, data)
            ws = self._ws
            if ws is not None and not ws.closed:
                try:
                    await ws.send_str(json.dumps(item, ensure_ascii=False))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # HTTP 路由
    # ------------------------------------------------------------------

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(WS_PATH, self._ws_handler)
        app.router.add_get(HEALTH_PATH, self._health_handler)
        return app

    def _authorized(self, request: web.Request) -> bool:
        token = self._cfg.token
        if not token:
            # 无 token 只允许回环地址
            peer = request.remote or ""
            return peer in ("127.0.0.1", "::1", "localhost")
        auth = request.headers.get("Authorization", "")
        provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        query_token = request.query.get("access_token", "")
        if provided and hmac.compare_digest(provided, token):
            return True
        return bool(query_token) and hmac.compare_digest(query_token, token)

    async def _health_handler(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=403)
        backend = self._backend
        try:
            status = await asyncio.get_running_loop().run_in_executor(
                None, backend.ping)
        except Exception as exc:
            status = {"pong": False, "error": str(exc)[:300]}
        return web.json_response({
            "ok": True,
            "uptime_sec": int(time.time() - self._started_at),
            "client_connected": self._ws is not None and not self._ws.closed,
            "buffered_events": len(self._buffer._items),
            "backend": status,
        })

    # ------------------------------------------------------------------
    # WebSocket 主流程
    # ------------------------------------------------------------------

    async def _ws_handler(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.Response(status=403, text="Forbidden")

        ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)

        # 握手：首帧必须是 hello（token 二次校验 + last_seq 重放水位）
        try:
            raw = await asyncio.wait_for(ws.receive_str(), timeout=15.0)
            hello = json.loads(raw)
        except Exception:
            await ws.send_str(json.dumps(protocol.make_hello_err("hello_expected")))
            await ws.close()
            return ws
        if hello.get("type") != protocol.TYPE_HELLO:
            await ws.send_str(json.dumps(protocol.make_hello_err("hello_expected")))
            await ws.close()
            return ws
        token = self._cfg.token
        if token and not hmac.compare_digest(str(hello.get("token") or ""), token):
            await ws.send_str(json.dumps(protocol.make_hello_err("auth_failed")))
            await ws.close()
            return ws

        # 单客户端：新连接顶替旧连接
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = ws

        self_info: Dict[str, Any] = {}
        try:
            self_info = await asyncio.get_running_loop().run_in_executor(
                None, self._backend.get_self_info)
        except Exception:
            pass

        last_seq = int(hello.get("last_seq") or 0)
        backlog = self._buffer.replay_since(last_seq)
        await ws.send_str(json.dumps(
            protocol.make_hello_ok(
                self._buffer.seq, self_info,
                replayed=len(backlog) if backlog else 0),
            ensure_ascii=False))
        if backlog:
            for item in backlog:
                await ws.send_str(json.dumps(item, ensure_ascii=False))
        # backlog 为 None = 缺口（缓冲覆盖不了），频道侧以桥水位为准继续收新事件

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # 持有任务引用，防止 GC 提前回收在途 RPC
                    task = asyncio.create_task(self._handle_frame(msg.data))
                    self._rpc_tasks.add(task)
                    task.add_done_callback(self._rpc_tasks.discard)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            if self._ws is ws:
                self._ws = None

        return ws

    async def _handle_frame(self, raw: str) -> None:
        """处理一帧 RPC 请求（并发执行，结果按 id 配对）。"""
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            return
        if req.get("type") != protocol.TYPE_RPC:
            return
        rpc_id = str(req.get("id") or "")
        method = str(req.get("method") or "")
        params = req.get("params") or {}
        try:
            data = await asyncio.get_running_loop().run_in_executor(
                None, self._backend.handle_rpc, method, params)
            resp = protocol.make_rpc_result(rpc_id, True, data=data)
        except BridgeError as exc:
            resp = protocol.make_rpc_result(rpc_id, False, error=str(exc))
        except Exception as exc:
            resp = protocol.make_rpc_result(rpc_id, False, error=f"桥内部错误: {exc}")
        ws = self._ws
        if ws is not None and not ws.closed:
            try:
                await ws.send_str(json.dumps(resp, ensure_ascii=False))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后端（微信连接）+ WS 服务 + 事件泵。"""
        self._loop = asyncio.get_running_loop()
        await self._loop.run_in_executor(None, self._backend.start)
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._cfg.host, self._cfg.port)
        await site.start()
        self._pump_task = asyncio.create_task(self._pump(), name="wcauto_bridge_pump")

    async def stop(self) -> None:
        """停止事件泵、WS 服务与后端。"""
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._backend.stop)
        except Exception:
            pass
