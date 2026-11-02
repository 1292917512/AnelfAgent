"""QQ WebSocket 单分片网关，按已受理的序列号恢复会话。"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from agent.channel.channel_types import ChannelStatus
from core.log import log

from .client import QQAPI


class Gateway:
    def __init__(self, api: QQAPI, intents: int, dispatch: Callable[[dict[str, Any]], Awaitable[None]],
                 set_status: Callable[[ChannelStatus], None]) -> None:
        self.api = api
        self.intents = intents
        self.dispatch = dispatch
        self.set_status = set_status
        self.session_id = ""
        self.seq: int | None = None
        self.online = False
        self.last_error = ""

    async def run(self) -> None:
        delay = 1.0
        async with aiohttp.ClientSession(trust_env=False) as http:
            while True:
                close_code: int | None = None
                try:
                    result = await self.api.request("GET", "/gateway")
                    url = result.get("url") or ""
                    if urlsplit(url).scheme != "wss" or not urlsplit(url).hostname:
                        raise ValueError("QQ 网关未提供 WSS 地址")
                    async with asyncio.timeout(20):
                        ws = await http.ws_connect(url, max_msg_size=1024 * 1024)
                    async with ws:
                        try:
                            await self._listen(ws)
                        finally:
                            close_code = ws.close_code
                except Exception as exc:
                    self.last_error = f"close={close_code}: {str(exc) or type(exc).__name__}"
                    log(f"QQ 官方网关断开: {self.last_error}", "WARNING", tag="通道")
                if self.online:
                    delay = 1.0
                self.online = False
                if close_code in {4001, 4002, 4010, 4011, 4012, 4013, 4014, 4914, 4915}:
                    self.set_status(ChannelStatus.ERROR)
                    return
                if close_code in {4006, 4007} or (close_code is not None and 4900 <= close_code <= 4913):
                    self.session_id, self.seq = "", None
                self.set_status(ChannelStatus.RECONNECTING)
                await asyncio.sleep(delay + random.uniform(0, delay / 4))
                delay = min(60.0, delay * 2)

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = await ws.receive_json(timeout=15)
        if hello.get("op") != 10:
            raise ValueError("QQ 网关缺少 Hello")
        interval = int(hello["d"]["heartbeat_interval"]) / 1000
        if interval <= 0 or interval > 300:
            raise ValueError("QQ 心跳间隔无效")
        self.online = False
        token = f"QQBot {await self.api.token()}"
        if self.session_id and self.seq is not None:
            await ws.send_json({"op": 6, "d": {"token": token, "session_id": self.session_id, "seq": self.seq}})
        else:
            self.seq = None
            await ws.send_json({"op": 2, "d": {"token": token, "intents": self.intents,
                                               "shard": [0, 1], "properties": {}}})
        pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        receiver = asyncio.create_task(self._receive(ws, interval, pending))
        worker = asyncio.create_task(self._dispatch_in_order(pending))
        try:
            done, _ = await asyncio.wait((worker, receiver), return_when=asyncio.FIRST_COMPLETED)
            for task in (worker, receiver):
                if task in done:
                    task.result()
        finally:
            receiver.cancel()
            worker.cancel()
            await asyncio.gather(receiver, worker, return_exceptions=True)

    async def _dispatch_in_order(self, pending: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            frame = await pending.get()
            if frame["t"] != "RESUMED":
                await self.dispatch(frame)
            if self.session_id:
                self.seq = max(self.seq or 0, frame["s"])

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse, interval: float,
                       pending: asyncio.Queue[dict[str, Any]]) -> None:
        clock = asyncio.get_running_loop().time
        next_beat, login_until = clock() + interval, clock() + 30
        awaiting_ack = False
        received_seq = self.seq
        while True:
            now = clock()
            if not self.online and now >= login_until:
                raise TimeoutError("QQ 网关登录超时")
            if now >= next_beat:
                if awaiting_ack:
                    raise TimeoutError("QQ 心跳未确认")
                await ws.send_json({"op": 1, "d": received_seq})
                awaiting_ack = True
                next_beat = now + interval
            deadline = next_beat if self.online else min(next_beat, login_until)
            try:
                frame = await ws.receive_json(timeout=max(0.001, deadline - clock()))
            except TimeoutError:
                continue
            op = frame["op"]
            if op == 11:
                awaiting_ack = False
            elif op == 1:
                await ws.send_json({"op": 1, "d": received_seq})
            elif op in (7, 9):
                if op == 9:
                    self.session_id, self.seq = "", None
                raise ConnectionError(f"QQ 网关请求重连 (op={op})")
            elif op == 0:
                if not isinstance(frame.get("s"), int):
                    raise ValueError("QQ Dispatch 缺少序列号")
                received_seq = frame["s"]
                event = frame.get("t")
                if event == "READY":
                    self.session_id = frame["d"]["session_id"]
                    self.seq = frame["s"]
                elif not self.session_id:
                    raise ConnectionError("QQ 网关在 Ready 前下发事件")
                else:
                    # 满载时从已受理的 seq 续传，不越过未入站事件，也不阻塞心跳。
                    pending.put_nowait(frame)
                if not self.online:
                    self.online, self.last_error = True, ""
                    self.set_status(ChannelStatus.RUNNING)
