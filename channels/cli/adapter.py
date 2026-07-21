"""CLI 频道 — 本地终端交互式入口（BaseChannel 实现）。

最简频道实现，验证 BaseChannel 抽象的可用性。

启动方式::

    python -m channels.cli
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from pydantic import Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import ChannelType
from agent.channel.schemas import (
    ChannelInfo,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
)


class CLIConfig(ChannelConfig):
    """CLI 频道配置。"""

    show_prompt: bool = Field(default=True, description="显示输入提示符")
    bot_name: str = Field(default="", description="Bot 显示名（留空自动从 config/character.json 读取）")


class CLIChannel(BaseChannel[CLIConfig]):
    """本地 CLI 频道。"""

    _entity_description = "命令行终端调试频道"

    # ---- BaseChannel 必填类属性 ----
    channel_id = "cli"
    display_name = "命令行"
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
        # 启动时确定 Bot 显示名（配置优先，其次 character.json）
        cfg = self.get_config()
        self._bot_name = cfg.bot_name or self._load_bot_name_from_character()

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
        """将消息段打印到终端。"""
        try:
            for seg in request.segments:
                if seg.type.value == "text":
                    print(f"\n[{self._bot_name}] {seg.content}\n")
                else:
                    # 非文本段（图片/文件等）打印路径提示
                    hint = seg.file_path or seg.content or f"<{seg.type.value}>"
                    print(f"\n[{self._bot_name}] [{seg.type.value}] {hint}\n")
            return SendResponse(success=True, message_id=f"cli-{int(time.time() * 1000)}")
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

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id=user_id,
            user_name=user_id,
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
    # 批准机制渲染
    # ------------------------------------------------------------------

    async def render_approval_prompt(self, ctx) -> SendRequest:
        """渲染批准提示（CLI y/n 提示）。"""
        from agent.channel.base import ApprovalPromptRenderContext
        from agent.channel.schemas import AdapterChannel, ChannelType, SendSegment

        text = (
            f"\n⚠️  工具调用需要批准\n"
            f"  工具: {ctx.tool_name}\n"
            f"  参数: {ctx.tool_args_summary[:200]}\n"
            f"  风险: {ctx.risk_level}\n"
            f"  原因: {ctx.reason}\n"
            f"\n"
            f"输入 'y' 允许, 'n' 拒绝, 超时 {ctx.timeout_seconds:.0f}s\n"
            f"[request_id: {ctx.request_id}] "
        )

        return SendRequest(
            adapter_key=self.channel_id,
            channel=AdapterChannel(
                channel_id="",  # 由 approval/gate.py 填充
                channel_type=ChannelType.PRIVATE,
            ),
            segments=[SendSegment(type="text", content=text)],
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _load_bot_name_from_character(self) -> str:
        """从 config/character.json 读取角色名（保留旧逻辑）。"""
        try:
            path = Path("config/character.json")
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
    """运行交互式 CLI。"""
    from agent.channel import get_channel_manager
    from agent.runtime.agent_app import get_agent_app

    channel = CLIChannel()
    cm = get_channel_manager()
    cm.register(channel)
    await channel.start()

    app = get_agent_app()

    print("=" * 50)
    print(f"  AnelfAgent CLI 模式 (BaseChannel)")
    print(f"  输入消息与 {channel._bot_name} 对话，输入 exit/quit/q 退出")
    print("=" * 50)

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n你: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已退出")
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            print("已退出")
            break

        if not user_input.strip():
            continue

        await app.send_message(
            user_id=user_id,
            content=user_input,
            user_name=user_name,
            to_me=True,
            adapter_key="cli",
        )

        await asyncio.sleep(0.5)


def main() -> None:
    """CLI 入口点。"""
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
