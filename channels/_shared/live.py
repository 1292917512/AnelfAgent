"""直播域公共块 — 频道适配器的直播配置热更 mixin 与弹幕冷却门控。

宿主（适配器）约定：
- ``self.config`` 含 ``live_mode`` / ``live_watch_rooms`` / ``live_danmaku_cooldown_seconds``；
- ``self.live_control`` 暴露 ``watched`` / ``mode_enabled`` / ``set_mode`` / ``sync_rooms``；
- ``self.channel_id`` 与 ``super().reload_config()``（BaseChannel 语义）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Protocol

from core.async_helper import spawn
from core.log import log


class LiveConfig(Protocol):
    @property
    def live_mode(self) -> bool: ...
    @property
    def live_watch_rooms(self) -> str: ...
    @property
    def live_danmaku_cooldown_seconds(self) -> int: ...


class LiveManager(Protocol):
    @property
    def watched(self) -> List[str]: ...
    @property
    def mode_enabled(self) -> bool: ...
    async def set_mode(self, enabled: bool) -> str: ...
    async def sync_rooms(self) -> None: ...


class LiveHotReloadMixin:
    """直播配置热更：reload diff 应用 / 观察列表解析 / 异步应用 / 持久化。"""

    channel_id: str
    @property
    def live_control(self) -> LiveManager:
        """宿主提供直播管理器。"""
        raise NotImplementedError

    @property
    def live_config(self) -> LiveConfig:
        """宿主提供直播配置视图。"""
        raise NotImplementedError

    def _reload_channel_config(self) -> bool:
        """宿主从配置存储重新物化频道配置。"""
        raise NotImplementedError

    def _on_config_changed(self, key: str, value: Any) -> None:
        """配置变更监听走 reload_config 统一入口（含直播 diff 应用）。"""
        self.reload_config()

    def reload_config(self) -> bool:
        """热重载配置：diff 直播模式与观察列表并即时应用（Web 表单/AI 工具热切换入口）。"""
        prev_mode = self.live_config.live_mode
        prev_rooms = self.live_control.watched
        ok = self._reload_channel_config()
        if not ok:
            return False
        if self.live_config.live_mode != prev_mode or (
            self.live_config.live_mode and self._parse_rooms() != prev_rooms
        ):
            self._schedule_live_apply()
        return True

    def _parse_rooms(self) -> List[str]:
        raw = str(self.live_config.live_watch_rooms or "")
        return [x.strip() for x in raw.split(",") if x.strip().isdigit()]

    def _schedule_live_apply(self) -> None:
        """在事件循环内异步应用直播配置变更（无循环环境静默跳过）。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _apply() -> None:
            if bool(self.live_config.live_mode) != self.live_control.mode_enabled:
                await self.live_control.set_mode(bool(self.live_config.live_mode))
            elif self.live_config.live_mode:
                await self.live_control.sync_rooms()

        spawn(_apply(), name=f"{self.channel_id}-live-apply")

    def persist_live_config(self, *, live_mode: Optional[bool] = None,
                            rooms: Optional[List[str]] = None) -> None:
        """直播模式/观察列表变更后写回统一配置（AI 工具与 Web 直播 API 同源的持久化入口）。"""
        from agent.channel.config import set_channel_config

        try:
            updates: Dict[str, Any] = {}
            if live_mode is not None:
                updates["live_mode"] = live_mode
            if rooms is not None:
                updates["live_watch_rooms"] = ",".join(rooms)
            if updates:
                set_channel_config(self.channel_id, **updates)
        except Exception as exc:
            log(f"{self.channel_id}直播: 配置持久化失败（运行时变更仍已生效）: {exc}",
                "DEBUG", tag="通道")

    def on_login_expired(self) -> None:
        """登录态失效（轮询检测到）：置 ERROR 交频道守护退避重启，detail 引导重新登录。"""
        from agent.channel.channel_types import ChannelStatus
        self._status = ChannelStatus.ERROR
        log(f"{self.channel_id}: 登录态失效，频道置 ERROR（请重新登录）", "WARNING", tag="通道")

    def live_danmaku_cooldown_seconds(self) -> int:
        return max(int(self.live_config.live_danmaku_cooldown_seconds), 0)

    def is_known_group(self, target_id: str) -> bool:
        """评论区/直播间目标按群语义（供回复路由 channel_type 推断）。"""
        return target_id.startswith(("comment:", "live:"))


def danmaku_cooldown_error(last_sent: Dict[str, float], room: str, cooldown_seconds: int) -> Optional[str]:
    """冷却门控：冷却中返回错误文案，可发送返回 None（last_sent 由调用方发送成功后更新）。"""
    if cooldown_seconds and time.time() - last_sent.get(room, 0.0) < cooldown_seconds:
        return f"直播间 {room} 弹幕冷却中（{cooldown_seconds}s 内已发送），请稍后再发"
    return None
