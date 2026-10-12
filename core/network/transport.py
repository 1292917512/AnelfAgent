"""HTTPX 路由传输层：请求级选择、连接失败回退与连接池生命周期。"""

from __future__ import annotations

import ssl
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from core.config import ConfigManager
from core.network.policy import ProxyMode, resolve_network_route

ProxySelection = ProxyMode | Callable[[], ProxyMode]

def _current_proxies() -> set[str | None]:
    return {None, str(ConfigManager.get("http_proxy", "") or "").strip().rstrip("/"), str(ConfigManager.get("https_proxy", "") or "").strip().rstrip("/")}


def _can_fallback(error: httpx.TransportError, request: httpx.Request) -> bool:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return False
        cause = cause.__cause__
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    return (
        request.method in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get("content-length", "0") == "0"
        and "transfer-encoding" not in request.headers
        and isinstance(error, (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError))
    )


def _attempt_request(request: httpx.Request, connect_timeout: float | None) -> httpx.Request:
    extensions = dict(request.extensions)
    if connect_timeout is not None:
        timeout = dict(extensions.get("timeout", {}))
        current = timeout.get("connect")
        timeout["connect"] = min(current, connect_timeout) if current is not None else connect_timeout
        extensions["timeout"] = timeout
    return httpx.Request(request.method, request.url, headers=request.headers, stream=request.stream, extensions=extensions)


@dataclass
class _SyncPool:
    transport: httpx.BaseTransport
    users: int = 0


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, release: Callable[[], None]) -> None:
        self.stream, self.release = stream, release

    def __iter__(self) -> Iterator[bytes]:
        yield from self.stream

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.release()
            self.release = lambda: None


class NetworkTransport(httpx.BaseTransport):
    """同步请求路由；在途响应保留原连接，配置变更后闲置旧池回收。"""

    def __init__(self, proxy_mode: ProxySelection = "system", **options: Any) -> None:
        self.mode, self.options = proxy_mode, options
        self._pools: dict[str | None, _SyncPool] = {}
        self._lock = threading.RLock()
        self._closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        route = resolve_network_route(str(request.url), self.mode() if callable(self.mode) else self.mode)
        for index, proxy in enumerate(route.proxies):
            request.extensions["network_route"] = {"path": "proxy" if proxy else "direct", "source": route.source, "attempts": index + 1}
            with self._lock:
                if self._closed:
                    raise RuntimeError("网络传输已关闭")
                for key, old in list(self._pools.items()):
                    if key not in _current_proxies() and old.users == 0:
                        del self._pools[key]
                        old.transport.close()
                pool = self._pools.get(proxy)
                if pool is None:
                    pool = self._pools[proxy] = _SyncPool(httpx.HTTPTransport(proxy=proxy, trust_env=True, **self.options))
                pool.users += 1
            def release(pool: _SyncPool = pool) -> None:
                with self._lock:
                    pool.users -= 1
            fallback = index + 1 < len(route.proxies)
            try:
                response = pool.transport.handle_request(_attempt_request(request, route.connect_timeout if fallback else None))
            except BaseException as exc:
                release()
                if fallback and isinstance(exc, httpx.TransportError) and _can_fallback(exc, request):
                    continue
                raise
            response.extensions["network_route"] = request.extensions["network_route"]
            assert isinstance(response.stream, httpx.SyncByteStream)
            response.stream = _SyncStream(response.stream, release)
            return response
        raise RuntimeError("网络路由为空")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for pool in self._pools.values():
                pool.transport.close()
            self._pools.clear()


@dataclass
class _AsyncPool:
    transport: httpx.AsyncBaseTransport
    users: int = 0


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, release: Callable[[], None]) -> None:
        self.stream, self.release = stream, release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            await self.stream.aclose()
        finally:
            self.release()
            self.release = lambda: None


class AsyncNetworkTransport(httpx.AsyncBaseTransport):
    """异步请求路由，客户端须由所属事件循环创建、使用和关闭。"""

    def __init__(self, proxy_mode: ProxySelection = "system", **options: Any) -> None:
        self.mode, self.options = proxy_mode, options
        self._pools: dict[str | None, _AsyncPool] = {}
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        route = resolve_network_route(str(request.url), self.mode() if callable(self.mode) else self.mode)
        if self._closed:
            raise RuntimeError("网络传输已关闭")
        retired = [self._pools.pop(key) for key, pool in list(self._pools.items()) if key not in _current_proxies() and pool.users == 0]
        for old in retired:
            await old.transport.aclose()
        for index, proxy in enumerate(route.proxies):
            request.extensions["network_route"] = {"path": "proxy" if proxy else "direct", "source": route.source, "attempts": index + 1}
            if self._closed:
                raise RuntimeError("网络传输已关闭")
            pool = self._pools.get(proxy)
            if pool is None:
                pool = self._pools[proxy] = _AsyncPool(httpx.AsyncHTTPTransport(proxy=proxy, trust_env=True, **self.options))
            pool.users += 1
            def release(pool: _AsyncPool = pool) -> None:
                pool.users -= 1
            fallback = index + 1 < len(route.proxies)
            try:
                response = await pool.transport.handle_async_request(_attempt_request(request, route.connect_timeout if fallback else None))
            except BaseException as exc:
                release()
                if fallback and isinstance(exc, httpx.TransportError) and _can_fallback(exc, request):
                    continue
                raise
            response.extensions["network_route"] = request.extensions["network_route"]
            assert isinstance(response.stream, httpx.AsyncByteStream)
            response.stream = _AsyncStream(response.stream, release)
            return response
        raise RuntimeError("网络路由为空")

    async def aclose(self) -> None:
        self._closed = True
        pools, self._pools = self._pools, {}
        for pool in pools.values():
            await pool.transport.aclose()
