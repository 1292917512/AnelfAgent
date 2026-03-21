"""频道基类 -- 所有平台适配器继承 BaseChannel。

每个频道声明自己的能力集（ChannelCapability），注册到 ChannelManager 后
能力方法自动注册为 EntityRegistry 工具，供 AI 通过两级发现机制按需使用。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from core.log import log
from core.entity import BaseEntity, EntityType

_AT_RE = re.compile(r'\[at_uid:([^\]]+)\]')


class ChannelStatus(str, Enum):
    """频道运行状态。"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class ChannelCapability(str, Enum):
    """频道能力枚举 -- 声明频道支持的操作。"""

    # 发送类
    SEND_TEXT = "send_text"
    SEND_PHOTO = "send_photo"
    SEND_VIDEO = "send_video"
    SEND_AUDIO = "send_audio"
    SEND_VOICE = "send_voice"
    SEND_FILE = "send_file"
    SEND_LOCATION = "send_location"
    SEND_ANIMATION = "send_animation"
    SEND_CONTACT = "send_contact"
    SEND_POLL = "send_poll"
    # 消息操作
    EDIT_MESSAGE = "edit_message"
    DELETE_MESSAGE = "delete_message"
    FORWARD_MESSAGE = "forward_message"
    PIN_MESSAGE = "pin_message"
    UNPIN_MESSAGE = "unpin_message"
    # 信息查询
    GET_CHAT_INFO = "get_chat_info"
    GET_CHAT_MEMBERS = "get_chat_members"
    GET_CHAT_ADMINS = "get_chat_admins"
    LIST_KNOWN_CHATS = "list_known_chats"
    # 群管理
    BAN_USER = "ban_user"
    UNBAN_USER = "unban_user"
    SET_CHAT_TITLE = "set_chat_title"
    SET_CHAT_DESCRIPTION = "set_chat_description"
    # 互动
    MESSAGE_REACTION = "message_reaction"
    # 高级
    REPLY_TO = "reply_to"
    INLINE_KEYBOARD = "inline_keyboard"
    STREAMING = "streaming"


def _ok(data: Optional[Dict] = None) -> str:
    return json.dumps({"success": True, **(data or {})}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


class BaseChannel(BaseEntity, ABC):
    """平台频道抽象基类。

    继承 BaseEntity，实例化时自动注册到 EntityRegistry（类型 ADAPTER）。
    子类声明 capabilities 集合，ChannelManager 根据能力集自动注册工具。
    """

    _entity_type = EntityType.ADAPTER
    _adapter_configs: Optional[Dict[str, Dict[str, Any]]] = None

    def __init__(self) -> None:
        self._status: ChannelStatus = ChannelStatus.STOPPED
        super().__init__()
        self._register_adapter_configs()

    # ------------------------------------------------------------------
    # 必须实现
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """频道唯一标识（如 "telegram", "http_api"）。"""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """频道显示名称。"""

    @property
    @abstractmethod
    def capabilities(self) -> Set[ChannelCapability]:
        """声明支持的操作集。"""

    @abstractmethod
    async def start(self) -> None:
        """启动频道。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止频道。"""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """发送文本消息（所有频道必须实现）。返回 JSON 结果。"""

    # ------------------------------------------------------------------
    # 可选能力（子类按需覆写，返回 JSON 字符串）
    # ------------------------------------------------------------------

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        """发送图片，可附带说明文字。"""
        return _err(f"{self.display_name} 不支持发送图片")

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        """发送视频，可附带说明文字。"""
        return _err(f"{self.display_name} 不支持发送视频")

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        """发送音频文件，可附带说明文字。"""
        return _err(f"{self.display_name} 不支持发送音频")

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        """发送语音消息。"""
        return _err(f"{self.display_name} 不支持发送语音")

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        """发送文件，可附带说明文字。"""
        return _err(f"{self.display_name} 不支持发送文件")

    async def send_location(self, chat_id: str, latitude: str, longitude: str, **kwargs: Any) -> str:
        """发送地理位置坐标。"""
        return _err(f"{self.display_name} 不支持发送位置")

    async def send_animation(self, chat_id: str, animation: str, caption: str = "", **kwargs: Any) -> str:
        """发送 GIF 动图，可附带说明文字。"""
        return _err(f"{self.display_name} 不支持发送动图")

    async def send_contact(self, chat_id: str, phone: str, first_name: str, last_name: str = "", **kwargs: Any) -> str:
        """发送联系人名片。"""
        return _err(f"{self.display_name} 不支持发送联系人")

    async def send_poll(self, chat_id: str, question: str, options: str, **kwargs: Any) -> str:
        """发送投票，选项用竖线分隔。"""
        return _err(f"{self.display_name} 不支持发送投票")

    async def edit_message(self, chat_id: str, message_id: str, text: str, **kwargs: Any) -> str:
        """编辑已发送的消息内容。"""
        return _err(f"{self.display_name} 不支持编辑消息")

    async def delete_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """删除指定消息。"""
        return _err(f"{self.display_name} 不支持删除消息")

    async def forward_message(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        """转发消息到另一个会话。"""
        return _err(f"{self.display_name} 不支持转发消息")

    async def pin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """置顶指定消息。"""
        return _err(f"{self.display_name} 不支持置顶消息")

    async def unpin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """取消置顶指定消息。"""
        return _err(f"{self.display_name} 不支持取消置顶")

    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        """查询会话详细信息（标题、类型、成员数等）。"""
        return _err(f"{self.display_name} 不支持查询会话信息")

    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        """查询会话成员列表。"""
        return _err(f"{self.display_name} 不支持查询成员列表")

    async def get_chat_admins(self, chat_id: str, **kwargs: Any) -> str:
        """查询会话管理员列表。"""
        return _err(f"{self.display_name} 不支持查询管理员")

    async def list_known_chats(self, **kwargs: Any) -> str:
        """列出所有已知会话（Bot 曾交互过的用户和群组）。"""
        return _err(f"{self.display_name} 不支持查询已知会话")

    async def ban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """封禁群组中的用户。"""
        return _err(f"{self.display_name} 不支持封禁用户")

    async def unban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """解除群组中用户的封禁。"""
        return _err(f"{self.display_name} 不支持解封用户")

    async def set_chat_title(self, chat_id: str, title: str, **kwargs: Any) -> str:
        """修改群组标题。"""
        return _err(f"{self.display_name} 不支持修改群标题")

    async def set_chat_description(self, chat_id: str, description: str, **kwargs: Any) -> str:
        """修改群组简介描述。"""
        return _err(f"{self.display_name} 不支持修改群简介")

    async def message_reaction(self, chat_id: str, message_id: str, emoji_id: str = "212", **kwargs: Any) -> str:
        """对指定消息添加表情回应。"""
        return _err(f"{self.display_name} 不支持表情回应")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @property
    def status(self) -> ChannelStatus:
        return self._status

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "key": self.channel_id,
            "name": self.display_name,
            "status": self._status.value,
            "capabilities": [c.value for c in self.capabilities],
        }

    # ------------------------------------------------------------------
    # 向后兼容（BaseAdapter 接口别名）
    # ------------------------------------------------------------------

    @property
    def key(self) -> str:
        return self.channel_id

    # ------------------------------------------------------------------
    # 频道本地配置
    # ------------------------------------------------------------------

    _channel_config: Dict[str, Any] = {}
    _channel_config_path: Optional[str] = None

    def load_channel_config(self, channel_dir: str) -> Dict[str, Any]:
        """从频道目录加载 channel_config.json。"""
        import os
        fp = os.path.join(channel_dir, "channel_config.json")
        self._channel_config_path = fp
        if os.path.exists(fp):
            try:
                raw = open(fp, encoding="utf-8").read()
                self._channel_config = json.loads(raw) if raw.strip() else {}
            except Exception as e:
                log(f"频道配置文件解析失败 ({fp}): {e}", "DEBUG")
                self._channel_config = {}
        return self._channel_config

    def save_channel_config(self) -> bool:
        """保存频道配置到 channel_config.json。"""
        if not self._channel_config_path:
            return False
        try:
            data = json.dumps(self._channel_config, indent=2, ensure_ascii=False)
            with open(self._channel_config_path, "wb") as f:
                f.write(data.encode("utf-8"))
            return True
        except Exception as e:
            log(f"频道配置保存失败: {e}", "DEBUG")
            return False

    def reload_config(self) -> bool:
        """重新加载 channel_config.json，使运行时配置生效。"""
        if not self._channel_config_path:
            return False
        import os
        if not os.path.exists(self._channel_config_path):
            return False
        try:
            raw = open(self._channel_config_path, encoding="utf-8").read()
            self._channel_config = json.loads(raw) if raw.strip() else {}
            log(f"频道配置已热重载: {self.channel_id}", "DEBUG")
            return True
        except Exception as e:
            log(f"频道配置热重载失败 ({self._channel_config_path}): {e}", "WARNING")
            return False

    def get_adapter_config(self, key: str, default: Any = None) -> Any:
        """优先从频道本地配置读取，回退到 ConfigManager。"""
        if key in self._channel_config:
            return self._channel_config[key]
        try:
            from core.config import ConfigManager
            return ConfigManager.get(key, default)
        except Exception:
            return default

    def set_adapter_config(self, key: str, value: Any) -> None:
        """设置频道本地配置值。"""
        self._channel_config[key] = value

    def _register_adapter_configs(self) -> None:
        configs = self._adapter_configs
        if not configs:
            return
        try:
            from core.config import register_configs
            register_configs(configs)
        except Exception:
            log(f"频道配置注册跳过: {self.__class__.__name__}", "DEBUG")

    # ------------------------------------------------------------------
    # @ 格式工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_at_mentions(text: str) -> str:
        """将 [at_uid:xxx] 转为纯文本 @uid。

        不支持原生 @ 的频道在发送前调用此方法，避免用户看到原始标记。
        """
        def _replacer(m: re.Match[str]) -> str:
            uid = m.group(1)
            if uid == "all":
                return "@全体成员"
            return f"@{uid}"
        return _AT_RE.sub(_replacer, text)

    # ------------------------------------------------------------------
    # 入站消息分发（子类收到消息后调用）
    # ------------------------------------------------------------------

    async def on_message(self, message: Any) -> None:
        """收到平台消息后调用，转发到 ChannelManager。"""
        from .manager import get_channel_manager
        await get_channel_manager().dispatch_inbound(self, message)
