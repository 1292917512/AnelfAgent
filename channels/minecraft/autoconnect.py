"""Minecraft 自动进服：发现局域网世界后主动连接，把进服开销移出对话回合。

卡顿案（2026-10-08 采样定案）的结构性修复：mineflayer 进服的世界数据
洪峰会拖住内置服务器数秒，若进服发生在对话回合中间（bot 掉线/重启后
的第一句话），玩家会把"进服卡顿"误读为"bot 说话卡顿"。本模块把进服
时机前移到"世界对局域网开放"那一刻——bot 自己走过来待命，回合内永远
不再发生隐式重连。

纪律：
- 默认关闭（opt-in 配置 auto_connect），不影响只把本频道当聊天桥的用户
- 连续断线超过防抖窗口才动手，AI 正在自己 connect_bot 的瞬态抖动不会
  触发重复进服（同名重连会把旧会话踢下线，必须避免双连）
- 发现失败/连接失败都进入冷却，不轮询轰炸组播端口
- 多世界时选最近广播的一个，确定性行为，不问用户
- 连接一律走回环地址：局域网世界开在玩家自己电脑上，而 MCP 主机白名单
  只放行回环；发现的局域网 IP 仅用于日志展示
- 执行器已在线（ALREADY_CONNECTED）视为达成状态，不重连、不进冷却
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Awaitable, Callable

from core.log import log

from .discovery import LanWorld

MCPToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Discover = Callable[[], Awaitable[list[LanWorld]]]
Announce = Callable[[str], Awaitable[None]]
Clock = Callable[[], float]

# 判定与节奏（模块级常量：自动进服是确定性本能，不给用户调参）
_DISCONNECT_DEBOUNCE_SECONDS = 10.0
_RETRY_COOLDOWN_SECONDS = 60.0
_CONNECT_TIMEOUT_SECONDS = 45.0
_DISCOVERY_TIMEOUT_SECONDS = 15.0

_JOIN_ANNOUNCE = "我来了！"

# get_connection_status 的 status 字段取值：disconnected/connecting/online/reconnecting
_CONNECT_STATE = "online"
# connect_bot 在已有在线 bot 时报错的标记，等同“已进服”
_ALREADY_CONNECTED = "ALREADY_CONNECTED"

_LOOPBACK_HOST = "127.0.0.1"


class AutoConnector:
    """按连接状态驱动的自动进服器。

    tick(status) 由频道轮询周期调用；发现与连接在后台任务执行，不阻塞
    聊天事件分发。单元测试注入 call/discover 假实现后直接喂状态序列。
    """

    def __init__(
        self,
        call: MCPToolCall,
        discover: Discover,
        announce: Announce | None = None,
        *,
        bot_username: Callable[[], str] | str = "AnelfBot",
        clock: Clock = time.monotonic,
    ) -> None:
        self._call = call
        self._discover = discover
        self._announce = announce
        self._bot_username = bot_username
        self._clock = clock
        self._disconnected_since: float | None = None
        self._cooldown_until = 0.0
        self._task: asyncio.Task[None] | None = None

    def _username(self) -> str:
        if callable(self._bot_username):
            return self._bot_username()
        return self._bot_username

    async def tick(self, state: str | None) -> None:
        """喂入当前连接状态；满足条件时后台启动发现+连接。"""
        now = self._clock()
        if state == _CONNECT_STATE:
            self._disconnected_since = None
            return
        if self._disconnected_since is None:
            self._disconnected_since = now
        if self._task is not None and not self._task.done():
            return  # 发现/连接进行中
        if now - self._disconnected_since < _DISCONNECT_DEBOUNCE_SECONDS:
            return  # 瞬态断线（如 AI 正在自己重连），先观察
        if now < self._cooldown_until:
            return  # 上次尝试失败，冷却中
        self._task = asyncio.create_task(self._join(), name="channel.minecraft.autoconnect")

    async def wait_idle(self) -> None:
        """测试辅助：等待进行中的后台任务收口。"""
        task = self._task
        if task is not None:
            with contextlib.suppress(Exception):
                await task

    async def _join(self) -> None:
        try:
            worlds = await asyncio.wait_for(self._discover(), timeout=_DISCOVERY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._cooldown(self._clock())
            log("Minecraft 自动进服: 局域网世界发现超时", "WARNING", tag="Minecraft")
            return
        except Exception as exc:
            self._cooldown(self._clock())
            log(f"Minecraft 自动进服: 世界发现失败: {exc}", "WARNING", tag="Minecraft")
            return
        if not worlds:
            self._cooldown(self._clock())
            return
        world = max(worlds, key=lambda w: w.last_seen)
        try:
            await asyncio.wait_for(
                self._call(
                    "connect_bot",
                    {
                        "host": _LOOPBACK_HOST,
                        "port": world.port,
                        "username": self._username(),
                        "auth": "offline",
                    },
                ),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if _ALREADY_CONNECTED in str(exc):
                # 执行器已有在线 bot（如 AI 抢先手动连上）：视为达成，静默复位
                self._disconnected_since = None
                return
            self._cooldown(self._clock())
            log(
                f"Minecraft 自动进服: 连接 {_LOOPBACK_HOST}:{world.port} 失败"
                f"（发现的世界 {world.host}:{world.port}）: {exc}",
                "WARNING",
                tag="Minecraft",
            )
            return
        self._disconnected_since = None
        log(f"Minecraft 自动进服: 已加入 {world.motd or '世界'} ({world.host}:{world.port})", "INFO", tag="Minecraft")
        if self._announce is not None:
            with contextlib.suppress(Exception):
                await self._announce(_JOIN_ANNOUNCE)

    def _cooldown(self, now: float) -> None:
        self._cooldown_until = now + _RETRY_COOLDOWN_SECONDS

    async def stop(self) -> None:
        """频道关停时取消进行中的发现/连接。"""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
