"""非 HTTP 协议的代理隧道；成功后连接所有权交给调用方。"""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

from python_socks.async_.asyncio import Proxy

from .policy import NetworkRoute, ProxyMode, resolve_network_route
from .status import proxy_observations


def resolve_socket_route(host: str, port: int, proxy_mode: ProxyMode = "direct") -> NetworkRoute:
    """按全局规则解析 TCP 目标，组件默认直连。"""
    address = f"[{host}]" if ":" in host else host
    return resolve_network_route(f"tcp://{address}:{port}", proxy_mode)


async def open_proxy_socket(host: str, port: int, proxy_url: str, *, timeout: float) -> socket.socket:
    """经 HTTP CONNECT 或 SOCKS5 建立非阻塞隧道，失败或取消时释放 socket。"""
    scheme = urlsplit(proxy_url).scheme
    if scheme == "https":
        raise ValueError("TCP 隧道支持 http://、socks5://、socks5h:// 代理，请调整全局代理地址")
    url = "socks5://" + proxy_url[len("socks5h://"):] if scheme == "socks5h" else proxy_url
    address = f"[{host}]" if ":" in host else host
    target = f"tcp://{address}:{port}"
    try:
        sock = await Proxy.from_url(url).connect(dest_host=host, dest_port=port, timeout=timeout)
    except Exception as exc:
        proxy_observations.record(proxy_url, target, error=exc)
        raise ConnectionError(f"代理隧道建立失败: {host}:{port} ({type(exc).__name__})") from None
    proxy_observations.record(proxy_url, target)
    return sock
