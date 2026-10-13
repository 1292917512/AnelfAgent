"""wcauto 桥协议 — 频道侧（AnelfAgent）与桥侧（Windows）共用的报文定义。

传输：单条 WebSocket（桥为服务端，频道为客户端），报文为 JSON 文本帧。
可靠性：事件带单调递增 seq，桥侧环形缓冲；频道断线重连时在 hello 里
带上 last_seq，桥重放缓冲区内 seq > last_seq 的事件。

本模块只用标准库，两侧都可直接 import。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = 1

# 报文类型
TYPE_HELLO = "hello"            # client -> server：握手（token + last_seq）
TYPE_HELLO_OK = "hello_ok"      # server -> client：握手成功（当前 seq + 自身信息）
TYPE_HELLO_ERR = "hello_err"    # server -> client：握手失败（鉴权/版本）
TYPE_RPC = "rpc"                # client -> server：方法调用
TYPE_RPC_RESULT = "rpc_result"  # server -> client：调用结果
TYPE_EVENT = "event"            # server -> client：事件推送（带 seq）

# 事件名
EVENT_MESSAGE = "message"  # 新消息（私聊/群聊）
EVENT_RECALL = "recall"    # 检测到撤回（附原文）
EVENT_STATUS = "status"    # 桥自身状态变化（监听启动/窗口不可见等）

# RPC 方法名（桥后端实现）
RPC_PING = "ping"
RPC_DOCTOR = "doctor"
RPC_GET_SELF_INFO = "get_self_info"
RPC_GET_SESSIONS = "get_sessions"
RPC_GET_MESSAGES = "get_messages"
RPC_SEARCH_CONTACT = "search_contact"
RPC_GET_GROUP_MEMBERS = "get_group_members"
RPC_LIST_LABELS = "list_labels"
RPC_LABEL_MEMBERS = "label_members"
RPC_DOWNLOAD_MEDIA = "download_media"
RPC_SEND_MSG = "send_msg"
RPC_SEND_AT = "send_at"
RPC_SEND_FILE = "send_file"
RPC_SEND_IMAGE = "send_image"
RPC_RECALL_LAST = "recall_last"
RPC_GET_RECALLED = "get_recalled"
RPC_GET_MOMENTS = "get_moments"
RPC_MOMENT_LIKE = "moment_like"
RPC_MOMENT_COMMENT = "moment_comment"
RPC_FORWARD = "forward_message"
RPC_LABEL_SEND = "label_send"


def make_hello(token: str, last_seq: int) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": TYPE_HELLO,
        "token": token or "",
        "last_seq": max(int(last_seq), 0),
    }


def make_hello_ok(seq: int, self_info: Dict[str, Any], replayed: int) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": TYPE_HELLO_OK,
        "seq": int(seq),
        "self_info": dict(self_info or {}),
        "replayed": int(replayed),
        "server_time": time.time(),
    }


def make_hello_err(error: str) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": TYPE_HELLO_ERR, "error": error}


def make_rpc(method: str, params: Optional[Dict[str, Any]] = None,
             rpc_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": TYPE_RPC,
        "id": rpc_id or uuid.uuid4().hex[:16],
        "method": method,
        "params": dict(params or {}),
    }


def make_rpc_result(rpc_id: str, ok: bool, data: Any = None,
                    error: str = "") -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": TYPE_RPC_RESULT,
        "id": rpc_id,
        "ok": bool(ok),
        "data": data if ok else None,
        "error": "" if ok else str(error),
    }


def make_event(seq: int, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": TYPE_EVENT,
        "seq": int(seq),
        "event": event,
        "data": dict(data),
        "ts": time.time(),
    }


class EventBuffer:
    """事件环形缓冲：容量有限 + seq 单调递增，供断线重放。

    容量耗尽时丢弃最旧事件；重放区间已出缓冲时调用方应视为缺口
    （频道侧以桥水位为准，缺口消息可经 get_messages RPC 补拉）。
    """

    def __init__(self, capacity: int = 2000) -> None:
        self._capacity = max(int(capacity), 100)
        self._items: List[Dict[str, Any]] = []
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """登记一个事件（分配 seq），返回完整报文。"""
        self._seq += 1
        item = make_event(self._seq, event, data)
        self._items.append(item)
        if len(self._items) > self._capacity:
            del self._items[: len(self._items) - self._capacity]
        return item

    def replay_since(self, last_seq: int) -> Optional[List[Dict[str, Any]]]:
        """返回 seq > last_seq 的缓冲事件；缓冲区已覆盖不了该水位返回 None。"""
        if not self._items:
            return [] if self._seq <= last_seq else None
        oldest = int(self._items[0]["seq"])
        if last_seq < oldest - 1 and last_seq != 0:
            return None
        return [item for item in self._items if int(item["seq"]) > last_seq]
