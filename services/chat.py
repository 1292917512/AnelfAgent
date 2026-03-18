"""聊天服务 -- 消息发送、历史加载、bot 名称获取。"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from core.log import log
from services._runtime import get_agent_app, get_runtime, is_ready


class ChatService:

    def is_ready(self) -> bool:
        return is_ready()

    async def load_history(
        self, scope_id: str = "web_user", limit: int = 50,
    ) -> List[dict]:
        """加载指定用户的历史会话记录。"""
        rt = get_runtime()
        if rt is None:
            return []
        return await rt.data_center.sqlite.fetch_conversation_with_id(
            scope_type="user", scope_id=scope_id, limit=limit,
        )

    async def send_message(
        self,
        text: str,
        *,
        images: Optional[list] = None,
        media_segments: Optional[list] = None,
        user_id: str = "web_user",
        user_name: str = "用户",
        adapter_key: str = "webui",
    ) -> None:
        """通过 AgentApp 发送一条消息。"""
        app = get_agent_app()
        if app is None:
            raise RuntimeError("AgentApp 尚未初始化")
        await app.send_message(
            user_id=user_id,
            content=text,
            user_name=user_name,
            to_me=True,
            images=images or None,
            media_segments=media_segments or None,
            adapter_key=adapter_key,
        )

    def register_output(self, output: Any, adapter_key: str = "webui") -> None:
        """将一个轻量频道注册到 ChannelManager。"""
        rt = get_runtime()
        if rt is None:
            raise RuntimeError("AgentRuntime 尚未初始化")
        if hasattr(output, "channel_id"):
            from agent.channel import get_channel_manager
            cm = get_channel_manager()
            if output.channel_id not in cm.list_channels():
                cm.register_lightweight(output)
        else:
            rt.respond.register_output(output, adapter_key=adapter_key)

    @staticmethod
    def get_bot_name() -> str:
        """从人设配置读取 bot 名称。"""
        try:
            from agent.config import get_config_provider
            data = get_config_provider().get_persona_config()
            if data.get("name"):
                return data["name"]
            for line in data.get("personality", []):
                if "名称" in line:
                    for sep in ("：", ":"):
                        if sep in line:
                            parts = line.split(sep)
                            for i, pt in enumerate(parts):
                                if "名称" in pt and i + 1 < len(parts):
                                    name = parts[i + 1].split(",")[0].split("，")[0].strip()
                                    if name:
                                        return name
        except Exception as e:
            log(f"获取 bot 名称失败: {e}", "DEBUG")
        return "Bot"
