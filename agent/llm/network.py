"""模型网络客户端：统一路由与节点缓存连接亲和。"""

from __future__ import annotations

import asyncio
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
_shared_slot: tuple[asyncio.AbstractEventLoop, AsyncNetworkTransport] | None = None


def _shared_transport() -> AsyncNetworkTransport:
    """无模型上下文调用方的共享传输：按全局代理策略路由，按事件循环缓存。

    传输的连接池绑定创建它的事件循环，循环更替时整体换新（旧循环已死，
    其池无需也不能跨循环关闭）。
    """
    global _shared_slot
    loop = asyncio.get_running_loop()
    with _session_lock:
        if _shared_slot is None or _shared_slot[0] is not loop:
            _shared_slot = (loop, AsyncNetworkTransport("system"))
        return _shared_slot[1]


class _SessionTransport(httpx.AsyncBaseTransport):
    """把 LiteLLM SDK 的共享 HTTP 会话转接到当前模型持有的连接池。

    SDK 会话是进程级全局：绑定模型上下文的请求走各自的传输（保持策略隔离
    与节点缓存亲和），上下文之外直用 litellm 的组件（记忆投影、后台评审等）
    回退到全局代理策略的共享传输。
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = _active_transport.get() or _shared_transport()
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
