"""真实回环 WebSocket 覆盖慢入站、断线续传、无效会话及关闭后的任务回收。"""

import asyncio
from contextlib import suppress
from typing import Any

import aiohttp
import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent.channel.channel_types import ChannelStatus
from channels.qqbot.client import QQAPI
from channels.qqbot.gateway import Gateway


async def test_slow_delivery_resume_and_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    logins: list[dict[str, Any]] = []
    beats: list[int | None] = []
    committed: list[int | None] = []
    delivered: list[int] = []
    states: list[ChannelStatus] = []
    release = asyncio.Event()
    final_ready = asyncio.Event()

    async def serve(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 50}})
        logins.append(await ws.receive_json())
        if len(logins) == 1:
            await ws.send_json({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "one"}})
            await ws.send_json({"op": 0, "s": 2, "t": "CHAT", "d": {}})
            for _ in range(2):
                beat = await ws.receive_json()
                beats.append(beat["d"])
                committed.append(gateway.seq)
                await ws.send_json({"op": 11})
            release.set()
            while True:
                beat = await ws.receive_json()
                await ws.send_json({"op": 11})
                if gateway.seq == 2:
                    break
            await ws.send_json({"op": 7})
        elif len(logins) == 2:
            await ws.send_json({"op": 0, "s": 3, "t": "CHAT", "d": {}})
            await ws.send_json({"op": 0, "s": 4, "t": "RESUMED", "d": ""})
            while True:
                beat = await ws.receive_json()
                await ws.send_json({"op": 11})
                if gateway.seq == 4:
                    break
            await ws.close(code=4006)
        else:
            await ws.send_json({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "two"}})
            await ws.receive_json()
            await ws.send_json({"op": 11})
            final_ready.set()
        await ws.close()
        return ws

    async def accept(frame: dict[str, Any]) -> None:
        await release.wait()
        delivered.append(frame["s"])

    app = web.Application()
    app.router.add_get("/gateway", serve)
    async with TestServer(app) as server:
        connect = aiohttp.ClientSession.ws_connect
        monkeypatch.setattr(aiohttp.ClientSession, "ws_connect",
                            lambda session, url, **kwargs: connect(session, server.make_url("/gateway"), **kwargs))
        def response(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("getAppAccessToken"):
                return httpx.Response(200, json={"access_token": "T", "expires_in": 60})
            return httpx.Response(200, json={"url": "wss://gateway.test/ws"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            gateway = Gateway(QQAPI(http, "A", "test"), 123, accept, states.append)
            task = asyncio.create_task(gateway.run())
            try:
                await asyncio.wait_for(final_ready.wait(), 10)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    assert beats == [2, 2] and committed == [1, 1] and delivered == [2, 3]
    assert [frame["op"] for frame in logins] == [2, 6, 2]
    assert logins[1]["d"]["session_id"] == "one" and logins[1]["d"]["seq"] == 2
    assert states.count(ChannelStatus.RUNNING) == 3 and states.count(ChannelStatus.RECONNECTING) >= 2
