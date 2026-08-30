"""SSE 订阅者注册中心（core 层共享设施）。

订阅注册表是频道与 web 层的公共下游：web/routers/chat.py 管理连接生命周期，
channels/webui 发送前检查在线客户端——放 core 层避免频道反向依赖 web 层。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from core.log import log

_subscribers: List[asyncio.Queue[Dict[str, Any]]] = []


def subscribe() -> "asyncio.Queue[Dict[str, Any]]":
    """注册一个订阅者队列（SSE 连接建立时调用）。"""
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=256)
    _subscribers.append(queue)
    return queue


def unsubscribe(queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    """注销订阅者（SSE 连接断开时调用）。"""
    try:
        _subscribers.remove(queue)
    except ValueError:
        pass  # 重复注销（连接异常路径）无害


def subscriber_count() -> int:
    """当前在线订阅者数量（频道发送前检查用；0 = 没有在线客户端）。"""
    return len(_subscribers)


def broadcast(event: Dict[str, Any]) -> None:
    """向所有订阅者推送事件。

    队列满时丢弃最旧帧保新帧：堆积的多是流式增量帧，
    丢旧增量不丢 reply/turn_end 等关键终态帧（前端不会卡在 sending 态）。
    """
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                log("SSE 队列满且无法腾位，丢弃一帧", "DEBUG", tag="SSE")
