"""频道管理器 -- 频道注册中心 + 入站分发 + 默认回复路由。

替代 AdapterManager（OutputProtocol 部分）和 Action 类。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Union

from core.entity import BaseEntity, EntityType
from core.log import log

from .base import BaseChannel, ChannelStatus


class ChannelManager(BaseEntity):
    """频道注册中心 + 入站分发 + 默认回复路由。"""

    _entity_type = EntityType.SERVICE
    _entity_description = "频道管理器 — 管理所有通信频道的注册、路由和生命周期"

    def __init__(self) -> None:
        self._channels: Dict[str, BaseChannel] = {}
        self._channel_map: Dict[str, str] = {}
        self._channel_map_max: int = 1000
        self._group_targets: set[str] = set()
        super().__init__()

    # ------------------------------------------------------------------
    # 频道注册
    # ------------------------------------------------------------------

    def register(self, channel: BaseChannel) -> None:
        """注册频道并自动注册其能力工具到 EntityRegistry。"""
        cid = channel.channel_id
        if cid in self._channels:
            log(f"频道 {cid} 已注册，跳过", "WARNING", tag="通道")
            return
        self._channels[cid] = channel
        log(f"频道已注册: {cid} ({channel.display_name})", tag="通道")
        try:
            from .tool_bridge import register_channel_tools
            register_channel_tools(channel)
        except Exception as exc:
            log(f"频道能力工具注册失败: {exc}", "WARNING", tag="通道")

    def register_lightweight(self, channel: Any) -> None:
        """注册轻量频道（仅需 channel_id 和 send_text），跳过能力工具注册。"""
        cid = channel.channel_id
        if cid in self._channels:
            return
        self._channels[cid] = channel  # type: ignore[assignment]
        name = getattr(channel, "display_name", cid)
        log(f"轻量频道已注册: {cid} ({name})", tag="通道")

    def unregister(self, channel_id: str) -> None:
        channel = self._channels.pop(channel_id, None)
        if channel:
            try:
                from .tool_bridge import unregister_channel_tools
                unregister_channel_tools(channel_id)
            except Exception as exc:
                log(f"频道工具注销失败: {exc}", "WARNING", tag="通道")
            log(f"频道已注销: {channel_id}", tag="通道")

    def get(self, channel_id: str) -> Optional[BaseChannel]:
        return self._channels.get(channel_id)

    def list_channels(self) -> Dict[str, BaseChannel]:
        return dict(self._channels)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """并发启动所有已注册频道（跳过 deferred_start），总耗时由最慢的单个频道决定。"""
        import asyncio
        tasks = [
            self._start_one(cid, ch)
            for cid, ch in self._channels.items()
            if not getattr(ch, '_deferred_start', False)
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _start_one(self, cid: str, channel: BaseChannel) -> None:
        """启动单个频道，捕获异常防止影响其他频道的并发启动。"""
        try:
            channel._status = ChannelStatus.STARTING
            await channel.start()
            if channel._status == ChannelStatus.STARTING:
                channel._status = ChannelStatus.RUNNING
            log(f"频道已启动: {cid} ({channel._status.value})", tag="通道")
        except Exception as exc:
            channel._status = ChannelStatus.ERROR
            log(f"频道启动失败: {cid} -> {exc}", "ERROR", tag="通道")

    async def stop_all(self) -> None:
        for cid, channel in self._channels.items():
            try:
                await channel.stop()
                channel._status = ChannelStatus.STOPPED
                log(f"频道已停止: {cid}", tag="通道")
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    log(f"频道停止失败: {cid} -> {exc}", "ERROR", tag="通道")

    async def start_channel(self, channel_id: str) -> bool:
        channel = self._channels.get(channel_id)
        if not channel:
            return False
        try:
            channel._status = ChannelStatus.STARTING
            await channel.start()
            if channel._status == ChannelStatus.STARTING:
                channel._status = ChannelStatus.RUNNING
            try:
                from .tool_bridge import register_channel_tools
                register_channel_tools(channel)
            except Exception as exc:
                log(f"频道能力工具注册失败: {exc}", "WARNING", tag="通道")
            return True
        except Exception as exc:
            channel._status = ChannelStatus.ERROR
            log(f"频道启动失败: {channel_id} -> {exc}", "ERROR", tag="通道")
            return False

    async def stop_channel(self, channel_id: str) -> bool:
        channel = self._channels.get(channel_id)
        if not channel:
            return False
        try:
            await channel.stop()
            channel._status = ChannelStatus.STOPPED
            try:
                from .tool_bridge import unregister_channel_tools
                unregister_channel_tools(channel_id)
            except Exception as exc:
                log(f"频道工具注销失败: {exc}", "WARNING", tag="通道")
            return True
        except Exception as exc:
            log(f"频道停止失败: {channel_id} -> {exc}", "ERROR", tag="通道")
            return False

    # ------------------------------------------------------------------
    # 入站分发（平台 → AgentApp）
    # ------------------------------------------------------------------

    async def dispatch_inbound(self, channel: BaseChannel, message: Any) -> None:
        """将平台消息转发到 AgentApp。"""
        from .schemas import AdapterMessage, ChannelType

        if not isinstance(message, AdapterMessage):
            return

        cid = channel.channel_id
        log(f"收到入站消息: [{cid}] {message.sender.user_name}({message.sender.user_id}): {message.content[:80]}", "DEBUG", tag="通道")
        user_id = message.sender.user_id

        trigger_mind = message.trigger_mind

        channel_key = f"{cid}:{message.channel.channel_id}"
        self._channel_map[channel_key] = cid
        # LRU 上限：超出时淘汰最早插入的条目
        if len(self._channel_map) > self._channel_map_max:
            excess = len(self._channel_map) - self._channel_map_max
            for k in list(self._channel_map.keys())[:excess]:
                del self._channel_map[k]
        if message.channel.channel_type == ChannelType.GROUP:
            self._group_targets.add(channel_key)

        from agent.runtime.agent_app import get_agent_app
        from .schemas import ChannelType as CT

        images = self._extract_images(message)
        media_segments = self._extract_media_segments(message)

        await get_agent_app().send_message(
            user_id=user_id,
            content=message.content,
            user_name=message.sender.user_name or user_id,
            group_id=message.channel.channel_id if message.channel.channel_type == CT.GROUP else 0,
            to_me=message.is_to_me,
            nickname=message.sender.user_name,
            images=images,
            media_segments=media_segments,
            adapter_key=cid,
            message_id=message.message_id,
            session_id=message.channel.channel_id,
            reply_to_id=message.reply_to_id,
            reply_content=message.reply_content,
            trigger_mind=trigger_mind,
        )

    # ------------------------------------------------------------------
    # 默认回复路由（Mind → 来源频道）
    # ------------------------------------------------------------------

    async def reply(self, anything: Any, content: str) -> None:
        """将 Mind 的回复路由到来源频道的 send_text。"""
        channel = self._resolve_channel(anything)
        if not channel:
            log("无法路由回复：未找到来源频道", "ERROR", tag="通道")
            raise RuntimeError("无法路由回复：未找到来源频道")
        log(f"发送回复: [{channel.channel_id}] {content[:80]}", "DEBUG", tag="通道")

        from agent.messages import EverythingGroup
        if isinstance(anything, EverythingGroup) and anything.group_id not in (0, "0", "", None):
            chat_id = str(anything.group_id)
            channel_type = "group"
        else:
            chat_id = str(anything.uid)
            channel_type = "private"

        reply_to = getattr(anything, "adapter_message_id", None) or None
        await channel.send_text(chat_id, content, reply_to=reply_to, channel_type=channel_type)

    async def stream_start(self, anything: Any) -> None:
        """流式回复开始（仅对支持流式的频道有效）。"""
        pass

    async def stream_chunk(self, chunk: str, anything: Any = None) -> None:
        pass

    async def stream_end(self, full_text: str, anything: Any = None) -> None:
        """流式回复结束，发送最终内容。"""
        if full_text.strip():
            await self.reply(anything, full_text)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def resolve_channel_type(self, channel_id: str, target_id: str) -> str:
        """根据历史记录判断 target_id 是群聊还是私聊。

        优先查运行时缓存（_group_targets），未命中时询问频道适配器（用于重启后主动发消息场景）。
        """
        key = f"{channel_id}:{target_id}"
        if key in self._group_targets:
            return "group"
        ch = self._channels.get(channel_id)
        if ch is not None and hasattr(ch, "is_known_group") and ch.is_known_group(target_id):  # type: ignore[union-attr]
            self._group_targets.add(key)
            return "group"
        return "private"

    def _resolve_channel(self, anything: Any) -> Optional[BaseChannel]:
        """从消息对象解析来源频道。解析失败时返回 None（拒绝发送）。"""
        adapter_key = getattr(anything, "adapter_key", None)
        if adapter_key and adapter_key in self._channels:
            return self._channels[adapter_key]
        uid = str(getattr(anything, "uid", "") or "")
        group_id = str(getattr(anything, "group_id", "") or "")
        for key, cid in self._channel_map.items():
            if key.endswith(f":{uid}") or key.endswith(f":{group_id}"):
                return self._channels.get(cid)
        log(
            f"回复路由解析失败：无法从消息对象确定来源频道 "
            f"(adapter_key={adapter_key}, uid={uid}, group_id={group_id})",
            "WARNING", tag="通道",
        )
        return None

    @staticmethod
    def _extract_images(message: Any) -> list:
        import os

        from agent.llm.types import ImageContent
        from .schemas import SegmentType

        images: list = []
        for seg in getattr(message, "segments", []):
            if seg.type != SegmentType.IMAGE:
                continue
            file_path = getattr(seg, "file_path", "")
            url = getattr(seg, "url", "")
            if file_path and os.path.isfile(file_path):
                images.append(ImageContent(data=file_path, is_url=False))
            elif url:
                is_url = url.startswith("http://") or url.startswith("https://")
                images.append(ImageContent(data=url, is_url=is_url))
        return images

    @staticmethod
    def _extract_media_segments(message: Any) -> list:
        """提取非图片类媒体段（语音、音频、视频、文件）。"""
        from .schemas import SegmentType
        media_types = {SegmentType.VOICE, SegmentType.AUDIO, SegmentType.VIDEO, SegmentType.FILE}
        return [
            seg for seg in getattr(message, "segments", [])
            if seg.type in media_types
        ]

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "channels": {
                cid: ch.get_status_info() for cid, ch in self._channels.items()
            },
            "route_map_size": len(self._channel_map),
        }


# ======================================================================
# 全局单例
# ======================================================================

_channel_manager: Optional[ChannelManager] = None


def get_channel_manager() -> ChannelManager:
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager()
    return _channel_manager
