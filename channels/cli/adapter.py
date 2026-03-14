"""CLI 频道 — 本地终端交互式入口。

提供交互式命令行，通过 BaseChannel 复用同一 AgentApp/存储/模型/工具系统。

启动方式::

    python -m channels.cli
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Set

from agent.core.channel.channel import BaseChannel, ChannelCapability, ChannelStatus, _ok


class CLIChannel(BaseChannel):
    """本地 CLI 频道。"""

    _entity_description = "命令行终端调试频道"

    def __init__(self) -> None:
        self._bot_name = _load_bot_name()
        super().__init__()

    @property
    def channel_id(self) -> str:
        return "cli"

    @property
    def display_name(self) -> str:
        return "命令行"

    @property
    def capabilities(self) -> Set[ChannelCapability]:
        return {ChannelCapability.SEND_TEXT}

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """将回复打印到终端。"""
        print(f"\n[{self._bot_name}] {text}\n")
        return _ok({"chat_id": chat_id})


def _load_bot_name() -> str:
    """从 config/character.json 读取角色名。"""
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


async def run_cli(user_id: str = "cli_user", user_name: str = "用户") -> None:
    """运行交互式 CLI。"""
    from agent.core.channel import get_channel_manager
    from agent.core.runtime.agent_app import get_agent_app

    channel = CLIChannel()
    cm = get_channel_manager()
    cm.register(channel)
    await channel.start()

    app = get_agent_app()

    print("=" * 50)
    print(f"  AnelfAgent CLI 模式")
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
