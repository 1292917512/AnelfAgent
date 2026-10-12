"""MCP HTTPX2 传输适配，共用 HTTPX 路由而不复制重试与代理规则。"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2

from .policy import ProxyMode
from .transport import AsyncNetworkTransport


class _RequestStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.stream:
            yield chunk


class _ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx.TransportError as exc:
            error_type = getattr(httpx2, type(exc).__name__, httpx2.TransportError)
            raise error_type(str(exc)) from exc

    async def aclose(self) -> None:
        await self.stream.aclose()


class AsyncNetworkTransport2(httpx2.AsyncBaseTransport):
    """转换请求与流接口，路由、池管理和安全回退由核心传输实现。"""

    def __init__(self, proxy_mode: ProxyMode = "system") -> None:
        self.transport = AsyncNetworkTransport(proxy_mode)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        assert isinstance(request.stream, httpx2.AsyncByteStream)
        try:
            response = await self.transport.handle_async_request(httpx.Request(
                request.method, str(request.url), headers=request.headers.raw,
                stream=_RequestStream(request.stream), extensions=request.extensions,
            ))
        except httpx.TransportError as exc:
            error_type = getattr(httpx2, type(exc).__name__, httpx2.TransportError)
            raise error_type(str(exc), request=request) from exc
        assert isinstance(response.stream, httpx.AsyncByteStream)
        return httpx2.Response(response.status_code, headers=response.headers.raw,
                               stream=_ResponseStream(response.stream), extensions=response.extensions)

    async def aclose(self) -> None:
        await self.transport.aclose()


def async_network_client2(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    *, proxy_mode: ProxyMode = "system", **kwargs: Any,
) -> httpx2.AsyncClient:
    """创建使用统一规则的 HTTPX2 客户端，由调用方关闭。"""
    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx2.AsyncClient(transport=AsyncNetworkTransport2(proxy_mode), trust_env=False, headers=headers, auth=auth, **kwargs)
