"""统一网络 SDK：组件选择策略，核心管理全局规则与请求回退。"""

from typing import Any

import httpx

from .diagnostics import NetworkProbeReport, NetworkProbeRequest, diagnose_network
from .policy import NetworkRoute, ProxyMode, get_proxy_url, resolve_network_route
from .transport import AsyncNetworkTransport, NetworkTransport, ProxySelection

__all__ = [
    "NetworkProbeReport", "NetworkProbeRequest", "diagnose_network",
    "AsyncNetworkTransport", "NetworkTransport", "NetworkRoute", "ProxyMode",
    "get_proxy_url", "resolve_network_route", "network_client", "async_network_client",
]


def network_client(*, proxy_mode: ProxySelection = "system", **kwargs: Any) -> httpx.Client:
    """创建由调用方关闭的 HTTPX 客户端，代理配置由统一策略读取。"""
    transport_options = {key: kwargs.pop(key) for key in ("verify", "cert", "limits", "http1", "http2") if key in kwargs}
    return httpx.Client(transport=NetworkTransport(proxy_mode, **transport_options), trust_env=False, **kwargs)


def async_network_client(*, proxy_mode: ProxySelection = "system", **kwargs: Any) -> httpx.AsyncClient:
    """创建由调用方关闭的异步 HTTPX 客户端。"""
    transport_options = {key: kwargs.pop(key) for key in ("verify", "cert", "limits", "http1", "http2") if key in kwargs}
    return httpx.AsyncClient(transport=AsyncNetworkTransport(proxy_mode, **transport_options), trust_env=False, **kwargs)
