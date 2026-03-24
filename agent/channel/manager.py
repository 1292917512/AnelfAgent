"""频道管理器 -- 频道注册中心 + 入站分发 + 默认回复路由。

替代 AdapterManager（OutputProtocol 部分）和 Action 类。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Union

from core.entity import BaseEntity, EntityType
from core.log import log
from core.tags import reply_to_tag, tag_label

from .channel import BaseChannel, ChannelStatus


@dataclass
class RoutePolicy:
    """路由策略（白名单/黑名单/require_to_me）。"""

    require_to_me: bool = False
    user_allowlist: Set[str] = field(default_factory=set)
    user_blocklist: Set[str] = field(default_factory=set)
    group_allowlist: Set[str] = field(default_factory=set)
    group_blocklist: Set[str] = field(default_factory=set)

    def check(self, user_id: str, group_id: str = "") -> bool:
        """检查用户/群组是否通过白名单/黑名单。不含 require_to_me 门控。"""
        if self.user_blocklist and user_id in self.user_blocklist:
            return False
        if self.user_allowlist and user_id not in self.user_allowlist:
            return False
        if group_id:
            if self.group_blocklist and group_id in self.group_blocklist:
                return False
            if self.group_allowlist and group_id not in self.group_allowlist:
                return False
        return True

    def should_trigger(self, is_to_me: bool) -> bool:
        """判断消息是否应触发 Mind 思考。require_to_me=False 时总触发。"""
        return not self.require_to_me or is_to_me


class ChannelManager(BaseEntity):
    """频道注册中心 + 入站分发 + 默认回复路由。"""

    _entity_type = EntityType.SERVICE
    _entity_description = "频道管理器 — 管理所有通信频道的注册、路由和生命周期"

    def __init__(self) -> None:
        self._channels: Dict[str, BaseChannel] = {}
        self._channel_map: Dict[str, str] = {}
        self._group_targets: set[str] = set()
        self._global_policy = RoutePolicy()
        self._channel_policies: Dict[str, RoutePolicy] = {}
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
            from .output_tools import register_channel_capability_tools
            register_channel_capability_tools()
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
            log(f"频道已注销: {channel_id}", tag="通道")

    def get(self, channel_id: str) -> Optional[BaseChannel]:
        return self._channels.get(channel_id)

    def list_channels(self) -> Dict[str, BaseChannel]:
        return dict(self._channels)

    # ------------------------------------------------------------------
    # 策略
    # ------------------------------------------------------------------

    def set_global_policy(self, policy: RoutePolicy) -> None:
        self._global_policy = policy

    def set_channel_policy(self, channel_id: str, policy: RoutePolicy) -> None:
        self._channel_policies[channel_id] = policy

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
            await asyncio.gather(*tasks)

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
        group_id = (
            message.channel.channel_id
            if message.channel.channel_type == ChannelType.GROUP
            else ""
        )

        policy = self._channel_policies.get(cid)
        if policy and not policy.check(user_id, group_id):
            return
        if not self._global_policy.check(user_id, group_id):
            return

        trigger_mind = message.trigger_mind
        if policy:
            trigger_mind = trigger_mind and policy.should_trigger(message.is_to_me)
        trigger_mind = trigger_mind and self._global_policy.should_trigger(message.is_to_me)

        channel_key = f"{cid}:{message.channel.channel_id}"
        self._channel_map[channel_key] = cid
        if message.channel.channel_type == ChannelType.GROUP:
            self._group_targets.add(channel_key)

        from agent.runtime.agent_app import get_agent_app
        from .schemas import ChannelType as CT

        from .media import ensure_local_media
        await ensure_local_media(message.segments)

        images = self._extract_images(message)
        media_segments = self._extract_media_segments(message)

        normalized_content = self._inject_reply_context(
            message.content,
            reply_to_id=message.reply_to_id,
            reply_content=message.reply_content,
        )

        await get_agent_app().send_message(
            user_id=user_id,
            content=normalized_content,
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
        """从消息对象解析来源频道。"""
        adapter_key = getattr(anything, "adapter_key", None)
        if adapter_key and adapter_key in self._channels:
            return self._channels[adapter_key]
        uid = str(getattr(anything, "uid", "") or "")
        group_id = str(getattr(anything, "group_id", "") or "")
        for key, cid in self._channel_map.items():
            if key.endswith(f":{uid}") or key.endswith(f":{group_id}"):
                return self._channels.get(cid)
        if self._channels:
            return next(iter(self._channels.values()))
        return None

    @staticmethod
    def _inject_reply_context(content: str, *, reply_to_id: str, reply_content: str) -> str:
        """统一注入 [reply_to:id] 前缀，避免频道侧重复拼接。"""
        text = content or ""
        if not reply_to_id:
            return text

        lines = text.splitlines()
        while lines and lines[0].lstrip().startswith("[reply_to:"):
            lines.pop(0)
        body = "\n".join(lines)

        header = tag_label(reply_to_tag.get_tag_name(), str(reply_to_id))
        preview = " ".join((reply_content or "").split()).strip()
        if preview:
            preview = preview[:200]
            header = f"{header}{preview}"

        return f"{header}\n{body}" if body else header

    @staticmethod
    def _extract_images(message: Any) -> list:
        from agent.llm.types import ImageContent
        from .schemas import SegmentType

        images: list = []
        for seg in getattr(message, "segments", []):
            if seg.type != SegmentType.IMAGE:
                continue
            file_path = getattr(seg, "file_path", "")
            url = getattr(seg, "url", "")
            if file_path:
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
