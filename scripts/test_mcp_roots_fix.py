"""端到端验证 MCP roots 回调修复。

复刻 entities/mcp/bridge.py 的连接方式（ClientSession + list_roots_callback），
直连 chrome-devtools-mcp@1.6.0，验证：
1. take_screenshot 保存到 workspace 内 -> 成功
2. take_screenshot 保存到 workspace 外 -> 仍被拒绝（防护栏保留）
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from entities.mcp.bridge import _list_roots_callback

WS = "/Users/wangchenglong/projects/AnelfAgent/workspace"
ALLOWED_PATH = f"{WS}/temp/test_roots_fix.png"
DENIED_PATH = "/Users/wangchenglong/test_roots_fix_denied.png"


async def main() -> int:
    params = StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "chrome-devtools-mcp@1.6.0",
            "--browserUrl",
            "http://127.0.0.1:9222",
        ],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, list_roots_callback=_list_roots_callback
        ) as session:
            result = await session.initialize()
            print(f"[1] 已连接 server: {result.serverInfo.name} {result.serverInfo.version}")

            # 打开测试页，确保有页面可截图
            nav = await session.call_tool(
                "new_page", {"url": f"file://{WS}/temp/group_analysis.html"}
            )
            print(f"[2] new_page: isError={nav.isError}")

            # 用例 A：workspace 内路径，应成功
            ok = await session.call_tool(
                "take_screenshot", {"filePath": ALLOWED_PATH}
            )
            text_a = ok.content[0].text if ok.content else ""
            print(f"[3] workspace 内截图: isError={ok.isError} | {text_a[:150]}")

            # 用例 B：workspace 外路径，应被拒绝
            denied = await session.call_tool(
                "take_screenshot", {"filePath": DENIED_PATH}
            )
            text_b = denied.content[0].text if denied.content else ""
            print(f"[4] workspace 外截图: isError={denied.isError} | {text_b[:200]}")

            if ok.isError:
                print("FAIL: workspace 内截图仍被拒绝")
                return 1
            if not denied.isError or "workspace roots" not in text_b:
                print("FAIL: workspace 外路径未被拒绝，防护栏失效")
                return 1
            print("PASS: 修复生效，且防护栏保留")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
