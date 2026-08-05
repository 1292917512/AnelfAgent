"""搜索兜底链路：百度搜索不可用时降级到 MiniMax Coding Plan（订阅配额）。

web_search 等同步工具经 asyncio.to_thread 调度，工作线程内无运行中的事件循环，
可安全使用 asyncio.run 驱动异步客户端；若处于事件循环内则放弃兜底。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from core.log import log

_FALLBACK_TIMEOUT = 15.0


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def minimax_search(query: str, max_results: int) -> Dict[str, Any]:
    """经 MiniMax Coding Plan 搜索，返回归一化结果；未配置/失败时抛异常。"""
    from entities.minimax.client import MiniMaxClient, normalize_search_results
    client = MiniMaxClient()
    if not client.coding_plan_configured:
        raise RuntimeError("MiniMax Coding Plan 未配置凭据（entities/minimax/config.json 的 coding_plan_api_key）")
    data = asyncio.run(client.coding_plan_search(query, timeout=_FALLBACK_TIMEOUT))
    return normalize_search_results(data, query, max_results)


def try_minimax_search(query: str, max_results: int) -> Optional[Dict[str, Any]]:
    """尝试经 MiniMax Coding Plan 搜索，凭据未配置/调用失败/处于事件循环内时返回 None。"""
    if _in_running_loop():
        log("MiniMax 搜索兜底跳过：当前处于事件循环内，无法同步驱动异步客户端", "DEBUG", tag="web")
        return None
    try:
        return minimax_search(query, max_results)
    except Exception as e:
        log(f"MiniMax 搜索兜底失败: {e}", "DEBUG", tag="web")
        return None
