"""工作台页面租约：区分页面在线与可能有人操作，不推断用户已经阅读或同意。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

_LEASE_SECONDS = 45.0
_IDLE_SECONDS = 300.0


@dataclass
class _Client:
    state: dict[str, Any]
    received_at: float


_clients: dict[str, _Client] = {}


def update_state(state: dict[str, Any]) -> None:
    """按页面实例刷新租约；关闭页面只撤销自己的租约。"""
    now = time.monotonic()
    _prune(now)
    client_id = state.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        return
    sequence = state.get("sequence")
    if not isinstance(sequence, int):
        return
    previous = _clients.get(client_id)
    if previous and sequence <= previous.state["sequence"]:
        return
    _clients[client_id] = _Client({**state, "updated_at": time.time()}, now)


def _prune(now: float) -> None:
    for key, client in list(_clients.items()):
        if now - client.received_at >= _LEASE_SECONDS:
            del _clients[key]


def _status(client: _Client, now: float) -> str:
    state = client.state
    if state.get("closed") is True or state.get("connected") is not True:
        return "offline"
    if state.get("visible") is not True or state.get("focused") is not True:
        return "background"
    idle = state.get("idle_seconds")
    if not isinstance(idle, (int, float)) or not math.isfinite(idle) or idle < 0:
        return "idle"
    return "idle" if idle + now - client.received_at >= _IDLE_SECONDS else "active"


def get_presence() -> dict[str, Any]:
    """返回最佳可交互页面的快照；在线、前台和近期操作仅为在场信号。"""
    now = time.monotonic()
    _prune(now)
    if not _clients:
        return {"available": False, "can_ask": False, "status": "offline"}
    priority = {"active": 3, "idle": 2, "background": 1, "offline": 0}
    client = max(_clients.values(), key=lambda item: (priority[_status(item, now)], item.received_at))
    status = _status(client, now)
    return {
        "available": status != "offline", "can_ask": status == "active", "status": status,
        "state": dict(client.state),
    }
