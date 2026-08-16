"""worker 侧事件转换 — NoneBot Event → 桥接线协议 JSON。

利用 NoneBot 事件基类的通用方法（get_user_id / get_message / is_tome 等），
将任意适配器的事件转换为平台无关的中性 JSON（协议转移层），
由父进程 ``wire_in.py`` 再转换为 AdapterMessage。

仅在 worker venv 中以脚本方式运行（裸导入同目录模块）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Type

from protocol import MSG_EVENT

# 消息段类型 → 线协议段类型
_SEGMENT_TYPE_MAP: Dict[str, str] = {
    "text": "text",
    "image": "image",
    "photo": "image",
    "img": "image",
    "at": "at",
    "mention": "at",
    "record": "voice",
    "voice": "voice",
    "audio": "audio",
    "video": "video",
    "file": "file",
    "document": "file",
    "location": "location",
}


def convert_event_to_wire(
    bot: Any,
    event: Any,
    adapter_keys: Dict[Type[Any], str],
) -> Optional[Dict[str, Any]]:
    """将 NoneBot Event + Bot 转换为线协议事件 payload。

    Args:
        bot: NoneBot Bot 实例
        event: NoneBot Event 实例
        adapter_keys: Adapter 类 → 注册表 key 映射（bot.py 注册时构建）

    Returns:
        线协议事件 dict；不可转换的事件返回 None
    """
    try:
        event_type = event.get_type()
    except Exception:  # noqa: BLE001 - 事件对象异常时静默丢弃
        return None

    if event_type == "message":
        return _convert_message_event(bot, event, adapter_keys)
    if event_type == "notice":
        return _convert_notice_event(bot, event, adapter_keys)
    return None


def get_adapter_key(bot: Any, adapter_keys: Dict[Type[Any], str]) -> str:
    """解析 Bot 所属适配器的注册表 key。"""
    adapter = getattr(bot, "adapter", None)
    if adapter is not None:
        key = adapter_keys.get(type(adapter))
        if key:
            return key
        try:
            return str(type(adapter).get_name()).lower().replace(" ", "_")
        except Exception:  # noqa: BLE001 - get_name 失败走兜底
            pass
    return "unknown"


def _convert_message_event(
    bot: Any, event: Any, adapter_keys: Dict[Type[Any], str]
) -> Optional[Dict[str, Any]]:
    """转换消息类型事件。"""
    try:
        user_id = event.get_user_id()
    except Exception:  # noqa: BLE001 - 无用户 ID 的事件无法归因
        return None

    adapter_key = get_adapter_key(bot, adapter_keys)
    session_id, session_type = _get_session_info(event)
    user_name = _extract_user_name(event, user_id)

    if session_type == "private":
        session_id = user_id

    text, segments, reply_to = _extract_message_content(event)

    message_id = ""
    for attr in ("message_id", "msg_id", "id"):
        mid = getattr(event, attr, None)
        if mid is not None:
            message_id = str(mid)
            break

    return {
        "type": MSG_EVENT,
        "bot_id": str(getattr(bot, "self_id", "") or ""),
        "adapter": adapter_key,
        "event_kind": "message",
        "message_id": message_id,
        "session": {"id": session_id, "type": session_type},
        "sender": {
            "user_id": user_id,
            "user_name": user_name,
            "platform": f"nb_{adapter_key}",
        },
        "text": text,
        "segments": segments,
        "is_to_me": _check_is_to_me(event, session_type),
        "reply_to": reply_to,
        "timestamp": _extract_timestamp(event),
    }


def _convert_notice_event(
    bot: Any, event: Any, adapter_keys: Dict[Type[Any], str]
) -> Optional[Dict[str, Any]]:
    """转换通知类型事件为可读文本消息。"""
    try:
        desc = event.get_event_description()
    except Exception:  # noqa: BLE001 - 描述失败的通知事件丢弃
        return None
    if not desc:
        return None

    adapter_key = get_adapter_key(bot, adapter_keys)
    try:
        user_id = event.get_user_id()
    except Exception:  # noqa: BLE001 - 通知事件允许无用户
        user_id = "system"

    session_id, session_type = _get_session_info(event)

    return {
        "type": MSG_EVENT,
        "bot_id": str(getattr(bot, "self_id", "") or ""),
        "adapter": adapter_key,
        "event_kind": "notice",
        "message_id": "",
        "session": {"id": session_id, "type": session_type},
        "sender": {"user_id": user_id, "user_name": "", "platform": f"nb_{adapter_key}"},
        "text": f"({desc})",
        "segments": [{"seg": "text", "text": f"({desc})"}],
        "is_to_me": True,
        "reply_to": "",
        "timestamp": _extract_timestamp(event),
    }


def _get_session_info(event: Any) -> tuple[str, str]:
    """从事件提取会话 ID 与类型（group / private）。"""
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return str(group_id), "group"

    guild_id = getattr(event, "guild_id", None)
    ch_id = getattr(event, "channel_id", None)
    if guild_id and ch_id:
        return f"{guild_id}:{ch_id}", "group"
    if ch_id:
        return str(ch_id), "group"

    try:
        session_id = event.get_session_id()
        if "group" in session_id.lower():
            for part in session_id.split("_"):
                if part.isdigit():
                    return part, "group"
            return session_id, "group"
        return session_id, "private"
    except Exception:  # noqa: BLE001 - session 解析失败走用户 ID 兜底
        pass

    try:
        return event.get_user_id(), "private"
    except Exception:  # noqa: BLE001
        return "unknown", "private"


def _extract_user_name(event: Any, fallback: str) -> str:
    """尝试从事件提取用户昵称。"""
    for attr in ("sender", "user"):
        sender = getattr(event, attr, None)
        if sender is None:
            continue
        if isinstance(sender, dict):
            return str(
                sender.get("card") or sender.get("nickname")
                or sender.get("name") or fallback
            )
        for name_attr in ("card", "nickname", "name", "user_name", "username"):
            name = getattr(sender, name_attr, None)
            if name:
                return str(name)
    return fallback


def _extract_message_content(event: Any) -> tuple[str, List[Dict[str, Any]], str]:
    """提取消息文本、线协议段列表与 reply_to。"""
    try:
        message = event.get_message()
    except Exception:  # noqa: BLE001 - 无消息体时尝试纯文本
        try:
            plain = event.get_plaintext()
            return plain, [{"seg": "text", "text": plain}], ""
        except Exception:  # noqa: BLE001
            return "", [], ""
    return _parse_nonebot_message(message)


def _parse_nonebot_message(message: Any) -> tuple[str, List[Dict[str, Any]], str]:
    """解析 NoneBot Message 为 (文本, 线协议段列表, reply_to)。"""
    text_parts: List[str] = []
    segments: List[Dict[str, Any]] = []
    reply_to = ""

    for seg in message:
        seg_type = str(getattr(seg, "type", ""))
        data: Dict[str, Any] = {}
        if hasattr(seg, "data") and isinstance(seg.data, dict):
            data = seg.data
        elif hasattr(seg, "model_dump"):
            data = seg.model_dump()
        elif hasattr(seg, "dict"):
            data = seg.dict()

        if seg_type.lower() == "reply":
            reply_to = str(data.get("id", "") or data.get("message_id", ""))
            continue

        wire_seg, text_repr = _convert_segment(seg_type, data, seg)
        if wire_seg is not None:
            segments.append(wire_seg)
            text_parts.append(text_repr)

    return "".join(text_parts), segments, reply_to


def _convert_segment(
    seg_type: str, data: Dict[str, Any], raw_seg: Any
) -> tuple[Optional[Dict[str, Any]], str]:
    """单个 NoneBot 消息段 → (线协议段, 文本表示)。"""
    mapped = _SEGMENT_TYPE_MAP.get(seg_type.lower(), "")

    if mapped == "text" or seg_type == "text":
        text = data.get("text", str(raw_seg) if raw_seg else "")
        if not text:
            return None, ""
        return {"seg": "text", "text": text}, text

    if mapped == "image":
        url = data.get("url", "") or data.get("file", "") or data.get("file_id", "")
        return (
            {"seg": "image", "url": str(url), "file": str(data.get("file", "")), "name": ""},
            "",
        )

    if mapped == "at":
        target = str(
            data.get("qq", "") or data.get("user_id", "") or data.get("target", "")
        )
        if not target:
            return None, ""
        at_text = f"[at_uid:{target}]"
        return {"seg": "at", "user_id": target}, at_text

    if mapped in ("voice", "audio", "video"):
        url = data.get("url", "") or data.get("file", "")
        return (
            {"seg": mapped, "url": str(url), "file": str(data.get("file", ""))},
            "",
        )

    if mapped == "file":
        url = data.get("url", "") or data.get("file", "")
        name = data.get("name", "") or data.get("file_name", "")
        return {"seg": "file", "url": str(url), "name": str(name)}, ""

    if mapped == "location":
        lat = data.get("latitude", data.get("lat", ""))
        lon = data.get("longitude", data.get("lon", ""))
        return (
            {"seg": "location", "text": f"{lat},{lon}"},
            f"[location:{lat},{lon}]",
        )

    # 未知类型：降级为文本
    try:
        text = str(raw_seg)
    except Exception:  # noqa: BLE001
        text = f"[{seg_type}]"
    if text:
        return {"seg": "text", "text": text}, text
    return None, ""


def _check_is_to_me(event: Any, session_type: str) -> bool:
    """检查事件是否与 Bot 相关。"""
    try:
        return bool(event.is_tome())
    except Exception:  # noqa: BLE001 - is_tome 失败按会话类型推断
        return session_type == "private"


def _extract_timestamp(event: Any) -> float:
    """尝试从事件提取时间戳。"""
    for attr in ("time", "timestamp", "created_at"):
        ts = getattr(event, attr, None)
        if ts is not None:
            try:
                return float(ts)
            except (ValueError, TypeError):
                continue
    return time.time()
