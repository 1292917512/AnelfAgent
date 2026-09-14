"""小度音箱局域网发现 — SSDP M-SEARCH（MediaRenderer 组播搜索）。

小度音箱的 DLNA 渲染端监听 :49494；组播不跨网段，跨网段场景经
provider 的手动 hosts 配置兜底。发现结果为 description.xml 的
LOCATION URL 列表（设备标识用 IP——小度每次重启 UDN 会变动）。
"""

from __future__ import annotations

import asyncio
import socket
from typing import Dict, List

from core.log import log

_LOG_TAG = "智能家居"

_SSDP_ADDR = "239.255.255.250"
_SSDP_PORT = 1900
_ST = "urn:schemas-upnp-org:device:MediaRenderer:1"
_XIAODU_PORT_MARK = ":49494/"

_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {_SSDP_ADDR}:{_SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    f"ST: {_ST}\r\n"
    "\r\n"
).encode()


def parse_response(data: bytes) -> Dict[str, str]:
    """解析 SSDP 响应头（大写键：LOCATION/ST/USN/...）。"""
    headers: Dict[str, str] = {}
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return headers
    for line in text.split("\r\n")[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().upper()] = value.strip()
    return headers


class _Collector(asyncio.DatagramProtocol):
    """SSDP 响应收集器（MX 内随机延迟，须持续收包到窗口结束）。"""

    def __init__(self) -> None:
        self.locations: List[str] = []

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        headers = parse_response(data)
        location = headers.get("LOCATION", "")
        if location and location not in self.locations:
            self.locations.append(location)


async def discover_renderers(timeout: float = 3.0) -> List[str]:
    """组播搜索 MediaRenderer，返回去重的 LOCATION URL 列表。"""
    loop = asyncio.get_running_loop()
    collector = _Collector()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: collector, sock=sock,
        )
        try:
            transport.sendto(_MSEARCH, (_SSDP_ADDR, _SSDP_PORT))
            await asyncio.sleep(timeout)
        finally:
            transport.close()
    except OSError as exc:
        log(f"SSDP 发现异常: {exc}", "WARNING", tag=_LOG_TAG)
        return []
    finally:
        sock.close()
    return collector.locations


def is_xiaodu_location(location: str) -> bool:
    """LOCATION 是否为小度音箱的 DLNA 描述地址（端口特征过滤）。"""
    return _XIAODU_PORT_MARK in location
