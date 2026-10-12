"""模型网络客户端：统一路由与节点缓存连接亲和。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any, Callable, Iterator, TypeVar

import httpx
import litellm

from core.network import AsyncNetworkTransport, ProxyMode, async_network_client


class ModelHTTPClient(httpx.AsyncClient):
    """LiteLLM 可共享的网络客户端，不在请求参数深拷贝时复制连接池。"""

    def __init__(self, mode: Callable[[], ProxyMode], timeout: float, pool_size: int = 100) -> None:
        self.network_transport = AsyncNetworkTransport(mode, limits=httpx.Limits(max_connections=pool_size, max_keepalive_connections=pool_size))
        super().__init__(transport=self.network_transport, trust_env=False, timeout=timeout)

    def __deepcopy__(self, memo: dict) -> ModelHTTPClient:
        return self


class ModelHTTPHandler(litellm.AsyncHTTPHandler):
    """原生 HTTP 协议的网络 handler，小连接池保持网关节点缓存亲和。"""

    def __init__(self, mode: Callable[[], ProxyMode], pool_size: int, timeout: float) -> None:
        self._mode, self._pool_size = mode, max(1, pool_size)
        super().__init__(timeout=timeout)

    def create_client(self, timeout: Any, event_hooks: Any = None,
                      ssl_verify: Any = None, shared_session: Any = None) -> httpx.AsyncClient:
        return async_network_client(
            proxy_mode=self._mode, timeout=timeout, follow_redirects=True,
            limits=httpx.Limits(max_connections=self._pool_size, max_keepalive_connections=self._pool_size),
            event_hooks=event_hooks, verify=True if ssl_verify is None else ssl_verify,
        )


_active_transport: ContextVar[httpx.AsyncBaseTransport | None] = ContextVar("model_network", default=None)
_session_lock = Lock()


class _SessionTransport(httpx.AsyncBaseTransport):
    """把 LiteLLM SDK 的共享 HTTP 会话转接到当前模型持有的连接池。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = _active_transport.get()
        if transport is None:
            raise RuntimeError("模型请求缺少网络上下文")
        return await transport.handle_async_request(request)


class _SessionClient(httpx.AsyncClient):
    """无连接资源的 SDK 会话入口，连接池由具体模型负责关闭。"""

    def __init__(self) -> None:
        super().__init__(transport=_SessionTransport(), trust_env=False, follow_redirects=True)


@contextmanager
def model_network(client: ModelHTTPClient | None) -> Iterator[None]:
    """绑定请求级网络上下文，跨 SDK 缓存和并发任务保持模型策略隔离。"""
    if client is None:
        yield
        return
    with _session_lock:
        if not isinstance(litellm.aclient_session, _SessionClient) or litellm.aclient_session.is_closed:
            litellm.aclient_session = _SessionClient()
    token = _active_transport.set(client.network_transport)
    try:
        yield
    finally:
        _active_transport.reset(token)


_Item = TypeVar("_Item")


async def network_stream(client: ModelHTTPClient | None, stream: AsyncIterator[_Item]) -> AsyncIterator[_Item]:
    """只在读取流事件时绑定网络上下文，不将上下文泄漏给事件消费方。"""
    while True:
        with model_network(client):
            try:
                item = await anext(stream)
            except StopAsyncIteration:
                return
        yield item
