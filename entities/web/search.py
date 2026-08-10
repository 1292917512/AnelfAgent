"""网页搜索：MiniMax Coding Plan（订阅配额，不计 API 调用费）。

web_search 等同步工具经 asyncio.to_thread 调度，工作线程内无运行中的事件循环，
可安全使用 asyncio.run 驱动异步客户端；处于事件循环内时抛出明确错误。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

_SEARCH_TIMEOUT = 15.0


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def minimax_search(query: str, max_results: int) -> Dict[str, Any]:
    """经 MiniMax Coding Plan 搜索，返回归一化结果；未配置/失败时抛异常。"""
    if _in_running_loop():
        raise RuntimeError("当前处于事件循环内，无法同步驱动异步搜索客户端")
    from entities.minimax.client import MiniMaxClient, normalize_search_results
    client = MiniMaxClient()
    if not client.coding_plan_configured:
        raise RuntimeError("MiniMax Coding Plan 未配置凭据（entities/minimax/config.json 的 coding_plan_api_key）")
    data = asyncio.run(client.coding_plan_search(query, timeout=_SEARCH_TIMEOUT))
    return normalize_search_results(data, query, max_results)
