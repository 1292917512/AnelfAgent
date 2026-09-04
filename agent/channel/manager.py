"""频道管理器 -- 频道注册中心 + 入站分发 + 默认回复路由。

替代 AdapterManager（OutputProtocol 部分）和 Action 类。
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Type

from core.entity import BaseEntity, EntityType
from core.log import log
from core.tags import strip_message_meta_tags

from .base import BaseChannel, ChannelStatus


def _channels_dir() -> Path:
    """频道目录（基于项目根的绝对路径，不依赖进程 CWD）。"""
    from core.path import project_root
    return Path(project_root()) / "channels"


def list_configured_channels() -> Dict[str, bool]:
    """扫描 channels/ 下所有频道目录，返回 {channel_id: enabled}（含未注册频道）。"""
    result: Dict[str, bool] = {}
    root = _channels_dir()
    if not root.is_dir():
        return result
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        if not (item / "adapter.py").exists():
            continue
        enabled = False
        cfg_file = item / "channel_config.json"
        if cfg_file.exists():
            try:
                data = json.loads(cfg_file.read_text("utf-8"))
                if isinstance(data, dict):
                    enabled = bool(data.get("enabled", False))
            except (json.JSONDecodeError, OSError):
                log(f"频道配置读取失败: {cfg_file}", "DEBUG", tag="通道")
        result[item.name] = enabled
    return result


def set_channel_enabled(channel_id: str, enabled: bool) -> bool:
    """持久化频道启用状态到 channel_config.json，并同步已注册频道的内存配置。

    仅编辑 enabled 字段，保留文件中的其余键与格式；配置文件不存在时返回 False。
    """
    cfg_file = _channels_dir() / channel_id / "channel_config.json"
    if not cfg_file.exists():
        return False
    data: Dict[str, Any] = {}
    try:
        raw = json.loads(cfg_file.read_text("utf-8"))
        if isinstance(raw, dict):
            data = raw
    except (json.JSONDecodeError, OSError):
        log(f"频道配置读取失败，将重建 enabled 字段: {cfg_file}", "DEBUG", tag="通道")
    data["enabled"] = bool(enabled)
    try:
        cfg_file.write_bytes(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
    except OSError as exc:
        log(f"频道启用状态落盘失败: {channel_id} -> {exc}", "WARNING", tag="通道")
        return False
    channel = get_channel_manager().get(channel_id)
    if channel is not None:
        try:
            channel.config.enabled = enabled
        except Exception as exc:
            log(f"频道内存配置同步失败: {channel_id} -> {exc}", "DEBUG", tag="通道")
    return True


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
            # 频道启动路径激活配置热更新监听（无事件循环期间登记的 watch 在此生效）
            self._ensure_config_watcher_started()
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
            self._ensure_config_watcher_started()
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

    async def activate_channel(self, channel_id: str) -> bool:
        """动态加载并启动一个未注册的频道（启动期因 enabled=false 被跳过的频道）。

        已注册时退化为 start_channel；目录/模块/频道类缺失或启动失败返回 False。
        """
        registered = self._channels.get(channel_id)
        if registered is not None:
            return await self.start_channel(channel_id)

        channel_dir = _channels_dir() / channel_id
        if not (channel_dir / "adapter.py").exists():
            log(f"频道激活失败，目录或 adapter.py 不存在: {channel_id}", "WARNING", tag="通道")
            return False
        try:
            mod = importlib.import_module(f"channels.{channel_id}.adapter")
        except Exception as exc:
            log(f"频道模块加载失败: {channel_id} - {exc}", "ERROR", tag="通道")
            return False

        channel_cls: Optional[Type[BaseChannel]] = getattr(mod, "CHANNEL_CLASS", None)
        if channel_cls is None:
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseChannel) and attr is not BaseChannel:
                    channel_cls = attr
                    break
        if channel_cls is None:
            log(f"频道类未找到: {channel_id}", "ERROR", tag="通道")
            return False

        try:
            # 配置在 BaseChannel.__init__ 中自动加载（channels/<id>/channel_config.json）
            self.register(channel_cls())
        except Exception as exc:
            log(f"频道实例化失败: {channel_id} - {exc}", "ERROR", tag="通道")
            return False
        return await self.start_channel(channel_id)

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
            message_kind=getattr(message.kind, "value", message.kind)
            if getattr(message, "kind", None) else "chat",
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
        session_id = str(getattr(anything, "session_id", "") or "")
        # 剥离 LLM 可能模仿历史格式带入的元数据标签（[message_id:xxx] 等）
        content = strip_message_meta_tags(content)
        await channel.send_text(
            chat_id, content, reply_to=reply_to, channel_type=channel_type,
            session_id=session_id,
        )

    async def stream_end(self, full_text: str, anything: Any = None) -> None:
        """流式回复结束，发送最终内容。"""
        if full_text.strip():
            await self.reply(anything, full_text)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_config_watcher_started() -> None:
        """激活配置热更新监听（best-effort，不影响频道启动）。"""
        try:
            from .config_watcher import get_config_watcher
            get_config_watcher().ensure_started()
        except Exception as exc:
            log(f"配置监听激活失败: {exc}", "DEBUG", tag="通道")

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
