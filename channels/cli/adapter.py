"""CLI 频道 — 本地终端调试入口。

Model Experience: 终端输入经 AgentApp 标准消息通道进入模型，回复输出到终端。
Token effect: 无额外固定提示；消息按普通会话计入。
Cache effect: 不触碰稳定前缀，输入进入会话动态上下文。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SegmentType,
    SendRequest,
    SendResponse,
)

from .config import CLIConfig


class CLIChannel(BaseChannel[CLIConfig]):
    """本地 CLI 频道。"""

    _entity_description = "命令行终端调试频道"

    # ---- BaseChannel 必填类属性 ----
    channel_id = "cli"
    display_name = "命令行"
    display_order = 90
    capabilities = {ChannelCapability.SEND_TEXT}
    metadata = ChannelMetadata(
        name="CLI",
        description="本地终端交互式调试频道",
        version="2.0.0",
        author="AnelfAgent",
        tags=["cli", "debug", "local"],
    )
    _Configs = CLIConfig

    def __init__(self) -> None:
        super().__init__()
        self._terminal_user_id: str = "cli_user"
        # 启动时确定 Bot 显示名（配置优先，其次 character.json）
        cfg = self.get_config()
        self._bot_name = cfg.bot_name or self._load_bot_name_from_character()

    def _on_config_changed(self, key: str, value: object) -> None:
        """配置热更后同步终端显示名。"""
        super()._on_config_changed(key, value)
        if key == "cli_bot_name":
            self._bot_name = self.config.bot_name or self._load_bot_name_from_character()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    # ------------------------------------------------------------------
    # 统一发送入口
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """将文本消息打印到终端，无法呈现的段类型显式失败。"""
        if (
            request.channel.channel_type != ChannelType.PRIVATE
            or request.channel.channel_id != self._terminal_user_id
        ):
            return SendResponse(success=False, error="目标不是当前终端会话")
        if not request.segments:
            return SendResponse(success=False, error="空消息")
        for seg in request.segments:
            if seg.type != SegmentType.TEXT:
                return SendResponse(success=False, error=f"段类型 {seg.type.value} 不受支持")
            if not seg.content.strip():
                return SendResponse(success=False, error="文本段内容为空")
        try:
            for seg in request.segments:
                print(f"\n[{self._bot_name}] {seg.content}\n")
            return SendResponse(success=True, message_id=f"cli-{time.time_ns()}")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # 信息查询
    # ------------------------------------------------------------------

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="cli_bot",
            user_name=self._bot_name,
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="CLI Session",
            channel_type=ChannelType.PRIVATE,
        )

    # ------------------------------------------------------------------
    # 健康探针（CLI 永远健康）
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            healthy=True,
            detail="CLI channel is always healthy (no external dependency)",
            last_success_at=time.time(),
        )


    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _load_bot_name_from_character(self) -> str:
        """从 config/character.json 读取角色名（保留旧逻辑）。"""
        try:
            from core.path import config_dir
            path = Path(config_dir()) / "character.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for line in data.get("personality", []):
                    if "名称" in line or "名字" in line:
                        for sep in ("：", ":"):
                            if sep in line:
                                parts = line.split(sep)
                                for i, p in enumerate(parts):
                                    if "名称" in p or "名字" in p:
                                        if i + 1 < len(parts):
                                            name = parts[i + 1].split(",")[0].split("，")[0].strip()
                                            if name:
                                                return name
        except Exception as e:
            from core.log import log
            log(f"CLI 角色名加载失败: {e}", "DEBUG")
        return "Bot"


# ======================================================================
# CLI 入口
# ======================================================================


async def run_cli(user_id: str = "cli_user", user_name: str = "用户") -> None:
    """在已启动的应用内接收终端输入。"""
    from agent.channel import get_channel_manager
    from agent.runtime.agent_app import get_agent_app

    cm = get_channel_manager()
    channel = cm.get("cli")
    if not isinstance(channel, CLIChannel):
        raise RuntimeError("CLI 频道未注册")
    channel._terminal_user_id = user_id
    if not await cm.start_channel("cli"):
        raise RuntimeError("CLI 频道启动失败")
    app = get_agent_app()
    print("=" * 50)
    print("  AnelfAgent CLI 模式 (BaseChannel)")
    print(f"  输入消息与 {channel._bot_name} 对话，输入 exit/quit/q 退出")
    print("=" * 50)
    lines: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def read_stdin() -> None:
        """从终端读取输入并交给应用事件循环。"""
        while True:
            try:
                line: str | None = input("\n你: " if channel.config.show_prompt else "")
            except (EOFError, KeyboardInterrupt, OSError):
                line = None
            try:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            except RuntimeError:
                return
            if line is None or line.strip().lower() in {"exit", "quit", "q"}:
                return

    threading.Thread(target=read_stdin, name="anelf.cli.stdin", daemon=True).start()
    while True:
        user_input = await lines.get()
        if user_input is None or user_input.strip().lower() in {"exit", "quit", "q"}:
            print("已退出")
            break
        if user_input.strip():
            await app.send_message(
                user_id=user_id,
                content=user_input,
                user_name=user_name,
                to_me=True,
                adapter_key="cli",
            )
