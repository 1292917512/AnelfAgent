"""
minimax_mcp_wrapper.py

minimax-coding-plan-mcp 包在 main() 调用前执行了 `print("Starting Minimax MCP server")`，
会将非 JSON 文本写入 stdout（MCP JSONRPC 通信通道），导致协议解析失败。
此 wrapper 在导入 server 之前将 builtins.print 重定向到 stderr，使 JSONRPC 通道保持干净。
"""

import builtins
import sys

_orig_print = builtins.print


def _stderr_print(*args, **kwargs):
    kwargs["file"] = sys.stderr
    _orig_print(*args, **kwargs)


builtins.print = _stderr_print

from minimax_mcp.server import main  # noqa: E402

main()
