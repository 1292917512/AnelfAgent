"""实时事件枢纽（core 层共享设施）— SSE / WebSocket 统一的订阅分发中心。

订阅注册表是频道与 web 层的公共下游：web/routers/chat.py（SSE）与
web/routers/chat_ws.py（WebSocket）管理各自的连接生命周期，channels/webui
发送前按 kind 检查在线客户端——放 core 层避免频道反向依赖 web 层。

语义：
- 订阅者携带身份（connection_id / client_kind）与 topic 过滤；
- 帧按类型分级背压：增量帧（delta 等）队列满时丢旧保新，终态帧
  （reply/media/turn_end 等）不可静默丢弃——队列被终态帧塞满时判定该
  订阅者死亡（Subscriber.dead，连接侧断开，客户端重连后经 /history 重同步）。
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.log import log

# 终态帧：丢失会导致前端状态错误（卡在 sending 态、漏一条回复），
# 背压时宁可判死订阅者也不可静默丢弃
TERMINAL_EVENTS: frozenset[str] = frozenset({
    "reply", "media", "turn_end", "ui_command", "approval_request", "share",
})

# 订阅者队列容量（与旧 sse_hub 一致）
_QUEUE_MAXSIZE = 256

_connection_seq = itertools.count(1)


@dataclass
class Subscriber:
    """一个实时事件订阅者（一条 SSE 或 WebSocket 连接）。"""

    queue: "asyncio.Queue[Dict[str, Any]]"
    topics: Optional[Set[str]] = None
    """订阅的事件名集合；None = 全部事件（现阶段前端按 chat_id 自过滤）。"""
    client_kind: str = "web"
    """客户端类别：web（浏览器 WebUI）/ desktop（桌面壳）——发送前在线判定按此分桶。"""
    connection_id: str = ""
    dead: bool = field(default=False)
    """True = 枢纽判定该订阅者死亡（终态帧塞爆队列），连接侧应注销并重同步。"""


_subscribers: List[Subscriber] = []


def subscribe(
    topics: Optional[Set[str]] = None,
    *,
    client_kind: str = "web",
    connection_id: str = "",
) -> Subscriber:
    """注册一个订阅者（SSE/WS 连接建立时调用）。"""
    sub = Subscriber(
        queue=asyncio.Queue(maxsize=_QUEUE_MAXSIZE),
        topics=set(topics) if topics else None,
        client_kind=client_kind,
        connection_id=connection_id or f"conn-{next(_connection_seq)}",
    )
    _subscribers.append(sub)
    return sub


def unsubscribe(sub: Subscriber) -> None:
    """注销订阅者（连接断开时调用；重复注销无害）。"""
    try:
        _subscribers.remove(sub)
    except ValueError:
        pass


def subscriber_count(*, client_kind: Optional[str] = None) -> int:
    """当前在线订阅者数量（频道发送前检查用；0 = 没有在线客户端）。

    client_kind 非空时只统计该类客户端（如 webui 频道只认 web 客户端）。
    """
    if client_kind is None:
        return len(_subscribers)
    return sum(1 for s in _subscribers if s.client_kind == client_kind)


def _accepts(sub: Subscriber, event_name: str) -> bool:
    return sub.topics is None or event_name in sub.topics


def publish(event: Dict[str, Any]) -> None:
    """向所有匹配订阅者推送事件（事件名取自 event["event"]）。

    背压策略：队列满时优先丢弃最旧的非终态帧腾位；若腾不出（队列里全是
    终态帧）且本次仍是终态帧，则标记订阅者死亡并丢弃——连接侧（SSE/WS
    发送循环）发现 dead 后断开连接，客户端重连并经 /history 重同步，
    好过前端在缺帧状态下继续渲染。
    """
    event_name = str(event.get("event", "message"))
    terminal = event_name in TERMINAL_EVENTS
    for sub in list(_subscribers):
        if sub.dead or not _accepts(sub, event_name):
            continue
        try:
            sub.queue.put_nowait(event)
            continue
        except asyncio.QueueFull:
            pass
        if _shed_oldest_droppable(sub):
            try:
                sub.queue.put_nowait(event)
                continue
            except asyncio.QueueFull:
                pass
        if terminal:
            sub.dead = True
            log(
                f"实时订阅者终态帧塞爆队列，判死断开: {sub.connection_id} "
                f"(kind={sub.client_kind}, event={event_name})",
                "WARNING", tag="RealtimeHub",
            )
        else:
            log("实时订阅队列满，丢弃一帧增量", "DEBUG", tag="RealtimeHub")


def _shed_oldest_droppable(sub: Subscriber) -> bool:
    """从队首起丢弃一帧非终态事件腾位（终态帧跳过保留），返回是否腾出空位。"""
    kept: List[Dict[str, Any]] = []
    shed = False
    while True:
        try:
            item = sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if not shed and str(item.get("event", "")) not in TERMINAL_EVENTS:
            shed = True  # 丢最旧的一帧增量
            continue
        kept.append(item)
    for item in kept:
        try:
            sub.queue.put_nowait(item)
        except asyncio.QueueFull:
            break
    return shed


def reset() -> None:
    """清空所有订阅者（测试用）。"""
    _subscribers.clear()
