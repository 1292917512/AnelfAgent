"""Telegram 启动失败、跨线程取消和资源清理的离线回归。"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from telegram.error import InvalidToken, TimedOut
from telegram.request import HTTPXRequest

from agent.channel.channel_types import ChannelStatus
from channels.telegram.adapter import TelegramAdapter
from channels.telegram.config import TelegramConfig
from core.entity import EntityRegistry


@dataclass
class Request:
    options: dict[str, Any]
    closed: bool = False

    async def shutdown(self) -> None:
        self.closed = True


@dataclass
class Bot:
    username: str = "test_bot"
    id: int = 1

    async def get_me(self) -> None:
        raise AssertionError("initialize 后不应重复请求 getMe")


@dataclass
class Updater:
    fail: bool = False
    running: bool = False
    stops: int = 0

    async def start_polling(self, **kwargs: Any) -> None:
        if self.fail:
            raise TimedOut()
        self.running = True

    async def stop(self) -> None:
        await asyncio.sleep(0.03)
        self.running = False
        self.stops += 1


@dataclass
class Application:
    fail_stage: str = ""
    delay: float = 0.03
    running: bool = False
    initialized: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    shutdowns: int = 0
    stops: int = 0
    bot: Bot = field(default_factory=Bot)
    updater: Updater = field(default_factory=Updater)

    def add_handler(self, handler: Any) -> None:
        pass

    async def initialize(self) -> None:
        self.initialized.set()
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail_stage == "initialize":
            raise TimedOut()

    async def start(self) -> None:
        if self.fail_stage == "start":
            raise TimedOut()
        self.running = True

    async def stop(self) -> None:
        self.running = False
        self.stops += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1


@pytest.fixture
async def channel(monkeypatch: pytest.MonkeyPatch):
    import telegram.ext
    import telegram.request

    app = Application()
    requests: list[Request] = []
    builder = Mock()
    for name in ("token", "request", "get_updates_request"):
        getattr(builder, name).return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(telegram.ext.Application, "builder", lambda: builder)

    def request(**kwargs: Any) -> Request:
        obj = Request(kwargs)
        requests.append(obj)
        return obj

    async def menu(bot: Any) -> None:
        pass

    monkeypatch.setattr(telegram.request, "HTTPXRequest", request)
    monkeypatch.setattr("channels.telegram.commands.register_commands", menu)
    monkeypatch.setattr("channels.telegram.adapter._STARTUP_TIMEOUT", 1)
    adapter = TelegramAdapter()
    adapter._config = TelegramConfig(bot_token="123456:TEST_TOKEN")
    try:
        yield adapter, app, requests, builder
    finally:
        await adapter.stop()
        EntityRegistry.unregister(adapter.get_entity_name())


async def test_start_and_stop_do_not_block_main_loop(channel) -> None:
    adapter, app, requests, builder = channel
    progress: list[str] = []
    task = asyncio.create_task(adapter.start())
    asyncio.get_running_loop().call_later(0.01, progress.append, "start")
    await task
    assert progress == ["start"]
    assert adapter.status is ChannelStatus.RUNNING
    await adapter.start()
    assert builder.build.call_count == 1
    asyncio.get_running_loop().call_later(0.01, progress.append, "stop")
    await adapter.stop()
    assert progress == ["start", "stop"]
    assert adapter._thread is None
    assert adapter._app is None
    assert len(requests) == 2 and all(request.closed for request in requests)
    assert app.updater.stops == app.stops == app.shutdowns == 1


@pytest.mark.parametrize("phase,expected", [
    ("initialize", "初始化/getMe"), ("start", "启动消息处理器"), ("polling", "启动轮询/deleteWebhook"),
])
async def test_failure_at_any_startup_phase_cleans_up(channel, phase: str, expected: str) -> None:
    adapter, app, requests, _ = channel
    app.fail_stage = phase
    app.updater.fail = phase == "polling"
    with pytest.raises(RuntimeError, match=expected) as error:
        await adapter.start()
    assert "TimedOut" in str(error.value)
    assert "api.telegram.org:443" in str(error.value)
    assert adapter.status is ChannelStatus.ERROR
    assert adapter._thread is None
    assert all(request.closed for request in requests)
    assert app.shutdowns == 1
    app.fail_stage = ""
    app.updater.fail = False
    await adapter.start()
    assert adapter.status is ChannelStatus.RUNNING


async def test_startup_budget_cancels_pending_initialization(channel) -> None:
    adapter, app, requests, _ = channel
    app.delay = 60
    with pytest.raises(RuntimeError, match="启动总等待时间已耗尽"):
        await adapter.start()
    assert app.cancelled
    assert adapter._thread is None
    assert not app.updater.running
    assert all(request.closed for request in requests)


async def test_cancelling_start_joins_thread_and_closes_requests(channel) -> None:
    adapter, app, requests, _ = channel
    app.delay = 60
    task = asyncio.create_task(adapter.start())
    assert await asyncio.to_thread(app.initialized.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.cancelled
    assert adapter._thread is None
    assert all(request.closed for request in requests)


async def test_stop_during_initialization_does_not_lose_stop_signal(channel) -> None:
    adapter, app, requests, _ = channel
    app.delay = 60
    task = asyncio.create_task(adapter.start())
    assert await asyncio.to_thread(app.initialized.wait, 1)
    await adapter.stop()
    with pytest.raises(RuntimeError, match="启动已取消"):
        await task
    assert adapter.status is ChannelStatus.STOPPED
    assert adapter._thread is None
    assert all(request.closed for request in requests)


async def test_unfinished_thread_is_retained_and_blocks_replacement(channel) -> None:
    adapter, app, _, builder = channel
    thread = Mock()
    thread.is_alive.return_value = True
    adapter._thread = thread
    adapter._app = app
    try:
        with pytest.raises(RuntimeError, match="保留句柄"):
            await adapter.stop()
        assert adapter._thread is thread and adapter._app is app
        with pytest.raises(RuntimeError):
            await adapter.start()
        builder.build.assert_not_called()
    finally:
        adapter._thread = None


async def test_both_request_pools_receive_proxy(channel) -> None:
    adapter, _, requests, _ = channel
    adapter._config = TelegramConfig(
        bot_token="123456:TEST_TOKEN", proxy_host="proxy.internal", proxy_port=8080,
    )
    await adapter.start()
    for request in requests:
        assert request.options["proxy"] == "http://proxy.internal:8080"


async def test_startup_error_redacts_token_and_keeps_error_type(channel) -> None:
    adapter, _, _, _ = channel
    token = "123456:DO_NOT_LOG"
    adapter._config = TelegramConfig(bot_token=token)
    message = adapter._format_startup_error(InvalidToken(f"Token {token} rejected"))
    assert token not in message
    assert "InvalidToken" in message


async def test_cancel_before_first_coroutine_step_closes_requests(channel, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, requests, _ = channel
    new_event_loop = asyncio.new_event_loop

    def stopped_loop() -> asyncio.AbstractEventLoop:
        loop = new_event_loop()
        loop.call_soon(adapter._stop_in_loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", stopped_loop)
    with pytest.raises(RuntimeError, match="就绪前已退出"):
        await adapter.start()
    assert adapter._thread is None
    assert all(request.closed for request in requests)


@pytest.mark.parametrize("method,stage", [("getMe", "初始化/getMe"), ("deleteWebhook", "启动轮询/deleteWebhook")])
async def test_real_sdk_timeout_closes_both_http_clients(
    monkeypatch: pytest.MonkeyPatch, method: str, stage: str,
) -> None:
    requests: list[HTTPXRequest] = []
    called: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        called.append(endpoint)
        if endpoint == method:
            raise httpx.ConnectTimeout("connection timed out", request=request)
        assert endpoint == "getMe"
        return httpx.Response(200, json={"ok": True, "result": {
            "id": 1, "is_bot": True, "first_name": "Test", "username": "test_bot",
        }})

    def request_factory(**kwargs: Any) -> HTTPXRequest:
        request = HTTPXRequest(**kwargs, httpx_kwargs={
            "transport": httpx.MockTransport(respond), "trust_env": False,
        })
        requests.append(request)
        return request

    monkeypatch.setattr("telegram.request.HTTPXRequest", request_factory)
    adapter = TelegramAdapter()
    adapter._config = TelegramConfig(bot_token="123456:TEST_TOKEN")
    try:
        with pytest.raises(RuntimeError, match=stage) as error:
            await adapter.start()
        assert "TimedOut" in str(error.value)
        assert adapter._thread is None
        assert called.count("getMe") == 1
        assert len(requests) == 2 and all(request._client.is_closed for request in requests)
    finally:
        await adapter.stop()
        EntityRegistry.unregister(adapter.get_entity_name())
