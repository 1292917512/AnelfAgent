"""Minecraft Java 局域网世界发现（UDP 组播 224.0.2.60:4445）。

Java 版客户端打开“对局域网开放”后，每 1.5 秒向组播地址广播
``[MOTD]<世界名>[/MOTD][AD]<端口>[/AD]``，游戏内局域网列表即依赖该机制。
监听到的世界按 (地址, 端口) 去重，供 AI 在用户未提供端口时自行找到世界。
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
from dataclasses import dataclass, field

_MULTICAST_GROUP = "224.0.2.60"
_MULTICAST_PORT = 4445
_ANNOUNCE_RE = re.compile(r"\[MOTD\](?P<motd>.*?)\[/MOTD\]\[AD\](?P<port>\d+)\[/AD\]")


@dataclass
class LanWorld:
    """一个正在广播的局域网世界。"""

    host: str
    port: int
    motd: str = ""
    last_seen: float = field(default_factory=time.time)

    def to_dict(self, now: float) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "motd": self.motd,
            "seen_seconds_ago": round(max(0.0, now - self.last_seen), 1),
        }


def parse_announcement(payload: bytes, host: str, now: float | None = None) -> LanWorld | None:
    """解析一条广播报文；格式不符返回 None。"""
    try:
        text = payload.decode("utf-8", errors="replace").strip("\x00").strip()
    except Exception:
        return None
    match = _ANNOUNCE_RE.search(text)
    if match is None:
        return None
    try:
        port = int(match.group("port"))
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    motd = match.group("motd").strip()
    return LanWorld(host=host, port=port, motd=motd, last_seen=now or time.time())


def listen_lan_announcements(wait_seconds: float = 3.0) -> list[LanWorld]:
    """加入组播监听一段时间，返回去重后的局域网世界列表。

    组播套接字与游戏客户端的监听共存（SO_REUSEADDR）；绑定失败（如端口被
    独占或防火墙拦截）时抛 OSError，由调用方转为工具错误提示。
    """
    if wait_seconds < 0.5:
        raise ValueError("监听时长至少 0.5 秒（局域网世界每 1.5 秒广播一次）")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", _MULTICAST_PORT))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton(_MULTICAST_GROUP) + socket.inet_aton("0.0.0.0"),
        )
        sock.settimeout(0.25)
        deadline = time.monotonic() + min(wait_seconds, 15.0)
        worlds: dict[tuple[str, int], LanWorld] = {}
        while time.monotonic() < deadline:
            try:
                payload, (host, _) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            world = parse_announcement(payload, host)
            if world is None:
                continue
            key = (world.host, world.port)
            if key in worlds:
                worlds[key].last_seen = world.last_seen
            else:
                worlds[key] = world
        return sorted(worlds.values(), key=lambda w: w.last_seen, reverse=True)
    finally:
        sock.close()


from entities._sdk import tool  # noqa: E402  （频道模块复用 entities 桥，同 adapter）


@tool(
    name="minecraft_discover_worlds",
    description="发现局域网广播的 Minecraft Java 世界（“对局域网开放”的世界，"
    "返回地址与端口；用户没说端口号时先查这里；connect_bot 报 ECONNREFUSED/"
    "连接失败说明旧端口已过期，立即用本工具重新发现，禁止重试旧端口）",
    group="mcp:minecraft",
    tags=["mcp", "minecraft"],
    timeout=20,
)
async def discover_worlds_tool(wait_seconds: float = 3.0) -> dict:
    """监听 Minecraft 局域网组播广播（224.0.2.60:4445）一段时间。

    Java 客户端每 1.5 秒广播一次世界地址与端口，游戏内局域网列表同源。
    发现多个世界时与用户确认去哪个；一个都没发现（专用服务器不广播）
    则请用户提供端口号。注册在 mcp:minecraft 分组：任何已激活该分组
    的会话（含网页端）都能直接调用。
    """
    try:
        worlds = await asyncio.to_thread(listen_lan_announcements, wait_seconds)
    except (OSError, ValueError) as exc:
        return {
            "success": False,
            "worlds": [],
            "error": f"局域网发现失败: {exc}",
            "hint": "请用户在游戏“对局域网开放”后提供端口号，或用 connect_bot 显式连接",
        }
    now = time.time()
    return {
        "success": True,
        "worlds": [w.to_dict(now) for w in worlds],
        "hint": (
            "按列表中的 port 用 connect_bot 连接（host 填 127.0.0.1）；多个世界时先与用户确认"
            if worlds
            else "未发现广播中的局域网世界（专用服务器不广播）；请用户确认已打开“对局域网开放”或直接提供端口号"
        ),
    }
