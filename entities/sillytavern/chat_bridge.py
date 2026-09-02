"""AI 与酒馆角色的对话桥接（调用 anelf-bridge 插件端点）。

架构：AI 是独立对话参与者。本模块把 AI 的消息 POST 到酒馆插件
/api/plugins/anelf-bridge/say，插件在酒馆服务端复用其内部角色卡 +
聊天文件 + 已配置的生成管道（main_api/chat_completion_source/secrets）
生成角色回复并写回聊天文件。AI 不需要人设——对话的是酒馆的角色。

插件源码随实体维护在 SillyTavern/plugins/anelf-bridge/，由
service.start() 启动前注入并在 config.yaml 开启 enableServerPlugins。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import config as st_config
from . import service
from .st_client import STError, get_st_client


class TavernChatError(RuntimeError):
    """对话桥接失败（酒馆未运行 / 插件未加载 / 生成失败等）。"""


def _running_base() -> str:
    if not service.is_running():
        raise TavernChatError("酒馆当前未运行。先用 sillytavern_start 启动它。")
    return st_config.base_url()


def bridge_health() -> Dict[str, Any]:
    """探测桥接插件是否就绪（插件加载 + 当前生成配置）。/health 是 GET。"""
    base = _running_base()
    try:
        return get_st_client().get(base, "/api/plugins/anelf-bridge/health") or {}
    except STError as e:
        raise TavernChatError(
            f"桥接插件不可达（可能未启用 enableBridgePlugin 或未重启酒馆）: {e}") from e


def chat_turn(avatar: str, message: str, chat_file: Optional[str] = None,
              name: str = "Anelf") -> Dict[str, Any]:
    """AI 对酒馆角色说一句话，返回角色回复（写回酒馆聊天文件）。

    Args:
        avatar: 角色标识（如 "Seraphina.png"）
        message: AI 说的话
        chat_file: 聊天文件名（不含 .jsonl；缺省按日期 "Anelf - YYYY-MM-DD"）
        name: AI 在酒馆中显示的名字
    """
    base = _running_base()
    if not message or not message.strip():
        raise TavernChatError("消息不能为空")
    payload: Dict[str, Any] = {"avatar": avatar, "message": message.strip(), "name": name}
    if chat_file:
        payload["chat_file"] = chat_file
    try:
        result = get_st_client().post(base, "/api/plugins/anelf-bridge/say", payload)
    except STError as e:
        raise TavernChatError(f"对话失败: {e}") from e
    if not isinstance(result, dict) or not result.get("ok"):
        raise TavernChatError(f"对话失败: {result}")
    return result
