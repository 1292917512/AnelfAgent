"""列出执行器连接类工具的参数 schema。"""

import asyncio

from mcp import ClientSession

from entities.mcp.config import load_mcp_config
from entities.mcp.schema import _parse_mcp_tool
from entities.mcp.transport import _create_transport


async def main() -> None:
    server = next(s for s in load_mcp_config().servers if s.name == "minecraft")
    async with _create_transport(server) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            for tool in (await session.list_tools()).tools:
                if tool.name in {"connect_bot", "connect_default", "disconnect_bot"}:
                    _, params = _parse_mcp_tool(tool)
                    print(tool.name, "->", [(p.name, p.type, p.required) for p in params],
                          "|", (tool.description or "")[:100])


if __name__ == "__main__":
    asyncio.run(main())
