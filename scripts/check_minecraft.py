"""检查 Minecraft 插件的真实 MCP 握手与工具契约，不连接游戏世界。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mcp import ClientSession

from channels.minecraft.protocol import ConnectionStatus, EventBatch
from channels.minecraft.reply_policy import COMPANION_GAME_TOOLS
from core.log import set_log_level
from entities.mcp.config import load_mcp_config
from entities.mcp.render import _render_call_result
from entities.mcp.schema import _parse_mcp_tool
from entities.mcp.transport import _create_transport

_REQUIRED_TOOLS = {
    "connect_default",
    "disconnect_bot",
    "get_connection_status",
    "get_observation",
    "get_inventory",
    "list_players",
    "follow_entity",
    "goto",
    "stop_pathfinding",
    "clear_control_states",
    "collect_block",
    "cancel_collect",
    "cancel_task",
    "chat",
    "whisper",
    "get_events",
    "mine_resources",
    "mining_status",
    "resume_mining",
    "return_from_mine",
    "action_status",
    "pause_action",
    "resume_action",
    "get_runtime_metrics",
    "configure_survival",
    "get_survival_status",
    "prepare_item",
    "production_status",
} | COMPANION_GAME_TOOLS
_REQUIRED_PARAMS = {
    "follow_entity": {"entityId", "range"},
    "chat": {"message"},
    "whisper": {"username", "message"},
    "get_events": {"since", "types", "limit"},
    "mine_resources": {"direction", "depth", "minY", "length", "item", "count"},
    "configure_survival": {"enabled", "intervalMs"},
    "prepare_item": {"item", "count", "mode"},
}


async def check(server_name: str) -> None:
    """通过项目所用传输建立真实会话并验证执行器的观察与事件协议。"""
    server = next((s for s in load_mcp_config().servers if s.name == server_name), None)
    if server is None or not server.enabled:
        raise ValueError(f"MCP 服务 {server_name} 未安装或未启用")
    async with _create_transport(server) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            info = await session.initialize()
            tool_params = {
                tool.name: {p.name for p in _parse_mcp_tool(tool)[1]} for tool in (await session.list_tools()).tools
            }
            available = set(tool_params)
            missing = _REQUIRED_TOOLS - available
            if missing:
                raise ValueError(f"执行器缺少工具: {', '.join(sorted(missing))}")
            for name, params in _REQUIRED_PARAMS.items():
                missing_params = params - tool_params[name]
                if missing_params:
                    raise ValueError(f"工具 {name} 参数解析不完整: {', '.join(sorted(missing_params))}")
            status = ConnectionStatus.model_validate_json(
                await _render_call_result(
                    await session.call_tool("get_connection_status", arguments={}),
                )
            )
            events = EventBatch.model_validate_json(
                await _render_call_result(
                    await session.call_tool("get_events", arguments={"types": ["__anelf_cursor__"], "limit": 1}),
                )
            )
            if status.survival is None:
                raise ValueError("执行器连接状态缺少本地生存能力声明")
            print(
                json.dumps(
                    {
                        "server": info.server_info.name,
                        "version": info.server_info.version,
                        "tools": len(available),
                        "required_tools": "passed",
                        "required_parameters": "passed",
                        "bot_status": status.state,
                        "event_cursor": events.next_since,
                        "survival_version": status.survival.version,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="minecraft")
    args = parser.parse_args()
    set_log_level("WARNING")
    try:
        asyncio.run(asyncio.wait_for(check(args.server), timeout=60))
    except Exception as exc:
        detail: BaseException = exc
        while isinstance(detail, BaseExceptionGroup) and detail.exceptions:
            detail = detail.exceptions[0]
        parser.exit(1, f"Minecraft 检查失败: {detail}\n")


if __name__ == "__main__":
    main()
