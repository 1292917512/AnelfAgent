"""NoneBot Event / Message ↔ AdapterMessage 双向转换器。

利用 NoneBot 事件基类提供的通用方法 (get_user_id, get_message, is_tome 等)
将任意 NoneBot 适配器的事件统一转换为 AnelfTools 的 AdapterMessage 模型。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from core.log import log

from agent.core.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
)


# 群成员名片缓存：{group_id: {user_id: nickname}}
_group_member_cache: Dict[str, Dict[str, str]] = {}


def get_cached_nickname(group_id: str, user_id: str) -> Optional[str]:
    """从缓存中获取群成员昵称。"""
    return _group_member_cache.get(group_id, {}).get(user_id)


def cache_nickname(group_id: str, user_id: str, nickname: str) -> None:
    """缓存群成员昵称。"""
    if group_id not in _group_member_cache:
        _group_member_cache[group_id] = {}
    _group_member_cache[group_id][user_id] = nickname


def convert_event(bot: Any, event: Any) -> Optional[AdapterMessage]:
    """将 NoneBot Event + Bot 转换为 AdapterMessage。

    仅处理 message 类型事件。通知和请求事件返回 None。
    """
    try:
        event_type = event.get_type()
    except Exception:
        return None

    if event_type == "message":
        return _convert_message_event(bot, event)

    if event_type == "notice":
        return _convert_notice_event(bot, event)

    return None


def _get_adapter_name(bot: Any) -> str:
    """从 Bot 对象提取适配器名称。"""
    adapter = getattr(bot, "adapter", None)
    if adapter is not None:
        cls_name = type(adapter).get_name()
        return str(cls_name).lower().replace(" ", "_")
    return "nonebot"


def _get_session_info(event: Any) -> Tuple[str, ChannelType]:
    """从事件提取会话 ID 和类型。

    NoneBot 事件的 get_session_id() 格式因适配器而异，
    这里尝试多种方式提取 group_id 和 channel_type。
    """
    channel_id = ""
    channel_type = ChannelType.PRIVATE

    # 尝试从事件属性直接获取
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return str(group_id), ChannelType.GROUP

    # 尝试 guild + channel 组合（频道类平台）
    guild_id = getattr(event, "guild_id", None)
    ch_id = getattr(event, "channel_id", None)
    if guild_id and ch_id:
        return f"{guild_id}:{ch_id}", ChannelType.GROUP

    if ch_id:
        return str(ch_id), ChannelType.GROUP

    # 回退到 session_id
    try:
        session_id = event.get_session_id()
        if "group" in session_id.lower():
            parts = session_id.split("_")
            for part in parts:
                if part.isdigit():
                    return part, ChannelType.GROUP
            return session_id, ChannelType.GROUP
        return session_id, ChannelType.PRIVATE
    except Exception:
        pass

    # 最终回退
    try:
        return event.get_user_id(), ChannelType.PRIVATE
    except Exception:
        return "unknown", ChannelType.PRIVATE


def _convert_message_event(bot: Any, event: Any) -> Optional[AdapterMessage]:
    """转换消息类型事件。"""
    try:
        user_id = event.get_user_id()
    except Exception:
        return None

    adapter_name = _get_adapter_name(bot)
    channel_id, channel_type = _get_session_info(event)

    user_name = _extract_user_name(event, user_id)

    # 缓存发送者昵称（群聊场景）
    if channel_type == ChannelType.GROUP and user_name and user_name != user_id:
        cache_nickname(channel_id, user_id, user_name)

    content, segments, reply_to_id = _extract_message_content(event, channel_id)
    is_to_me = _check_is_to_me(event)

    message_id = ""
    for attr in ("message_id", "msg_id", "id"):
        mid = getattr(event, attr, None)
        if mid is not None:
            message_id = str(mid)
            break

    if channel_type == ChannelType.PRIVATE:
        channel_id = user_id

    return AdapterMessage(
        message_id=message_id,
        sender=AdapterUser(
            platform=f"nb_{adapter_name}",
            user_id=user_id,
            user_name=user_name,
        ),
        channel=AdapterChannel(
            channel_id=channel_id,
            channel_type=channel_type,
        ),
        content=content,
        segments=segments,
        is_to_me=is_to_me,
        timestamp=_extract_timestamp(event),
        reply_to_id=reply_to_id,
    )


def _convert_notice_event(bot: Any, event: Any) -> Optional[AdapterMessage]:
    """转换通知类型事件为可读文本消息。"""
    try:
        desc = event.get_event_description()
    except Exception:
        return None

    if not desc:
        return None

    adapter_name = _get_adapter_name(bot)
    try:
        user_id = event.get_user_id()
    except Exception:
        user_id = "system"

    channel_id, channel_type = _get_session_info(event)

    return AdapterMessage(
        sender=AdapterUser(
            platform=f"nb_{adapter_name}",
            user_id=user_id,
        ),
        channel=AdapterChannel(
            channel_id=channel_id,
            channel_type=channel_type,
        ),
        content=f"({desc})",
        segments=[MessageSegment(type=SegmentType.TEXT, content=f"({desc})")],
        is_to_me=True,
        timestamp=_extract_timestamp(event),
    )


def _extract_user_name(event: Any, fallback: str = "") -> str:
    """尝试从事件中提取用户昵称。"""
    for attr in ("sender", "user"):
        sender = getattr(event, attr, None)
        if sender is None:
            continue
        if isinstance(sender, dict):
            return sender.get("card") or sender.get("nickname") or sender.get("name") or fallback
        for name_attr in ("card", "nickname", "name", "user_name", "username"):
            name = getattr(sender, name_attr, None)
            if name:
                return str(name)
    return fallback


def _extract_message_content(
    event: Any,
    group_id: str = "",
) -> Tuple[str, List[MessageSegment], str]:
    """从事件中提取消息内容、消息段列表和 reply_to_id。"""
    try:
        message = event.get_message()
    except Exception:
        try:
            plain = event.get_plaintext()
            return plain, [MessageSegment(type=SegmentType.TEXT, content=plain)], ""
        except Exception:
            return "", [], ""

    return _parse_nonebot_message(message, group_id)


def _parse_nonebot_message(
    message: Any,
    group_id: str = "",
) -> Tuple[str, List[MessageSegment], str]:
    """解析 NoneBot Message 对象为 (纯文本, 消息段列表, reply_to_id)。"""
    text_parts: List[str] = []
    segments: List[MessageSegment] = []
    reply_to_id = ""

    for seg in message:
        seg_type = seg.type if hasattr(seg, "type") else str(getattr(seg, "type", ""))
        seg_data: Dict[str, Any] = {}

        if hasattr(seg, "data") and isinstance(seg.data, dict):
            seg_data = seg.data
        elif hasattr(seg, "model_dump"):
            seg_data = seg.model_dump()
        elif hasattr(seg, "dict"):
            seg_data = seg.dict()

        # 提取 reply 段的 ID
        if seg_type.lower() == "reply":
            reply_to_id = str(seg_data.get("id", "") or seg_data.get("message_id", ""))
            continue

        converted = _convert_segment(seg_type, seg_data, seg, group_id)
        if converted:
            seg_model, text_repr = converted
            segments.append(seg_model)
            text_parts.append(text_repr)

    return "".join(text_parts), segments, reply_to_id


# 消息段类型映射
_SEGMENT_TYPE_MAP: Dict[str, SegmentType] = {
    "text": SegmentType.TEXT,
    "image": SegmentType.IMAGE,
    "photo": SegmentType.IMAGE,
    "img": SegmentType.IMAGE,
    "at": SegmentType.AT,
    "mention": SegmentType.AT,
    "record": SegmentType.VOICE,
    "voice": SegmentType.VOICE,
    "audio": SegmentType.AUDIO,
    "video": SegmentType.VIDEO,
    "file": SegmentType.FILE,
    "document": SegmentType.FILE,
    "location": SegmentType.LOCATION,
}


def _convert_segment(
    seg_type: str,
    data: Dict[str, Any],
    raw_seg: Any,
    group_id: str = "",
) -> Optional[Tuple[MessageSegment, str]]:
    """将单个 NoneBot 消息段转换为 AnelfTools MessageSegment。"""
    mapped_type = _SEGMENT_TYPE_MAP.get(seg_type.lower())

    if mapped_type == SegmentType.TEXT or seg_type == "text":
        text = data.get("text", str(raw_seg) if raw_seg else "")
        if not text:
            return None
        return MessageSegment(type=SegmentType.TEXT, content=text), text

    if mapped_type == SegmentType.IMAGE:
        url = data.get("url", "") or data.get("file", "") or data.get("file_id", "")
        return (
            MessageSegment(type=SegmentType.IMAGE, url=url, file_path=data.get("file", "")),
            "",
        )

    if mapped_type == SegmentType.AT:
        target = str(data.get("qq", "") or data.get("user_id", "") or data.get("target", ""))
        if target == "all":
            at_text = "[@id:all;nickname:全体成员@]"
        else:
            # 尝试获取缓存的昵称
            nickname = get_cached_nickname(group_id, target) if group_id else ""
            if nickname:
                at_text = f"[@id:{target};nickname:{nickname}@]"
            else:
                at_text = f"[@id:{target}@]"
        return (
            MessageSegment(type=SegmentType.AT, at_user_id=target, content=at_text),
            at_text,
        )

    if mapped_type == SegmentType.VOICE:
        url = data.get("url", "") or data.get("file", "")
        return (
            MessageSegment(type=SegmentType.VOICE, url=url, file_path=data.get("file", "")),
            "",
        )

    if mapped_type == SegmentType.AUDIO:
        url = data.get("url", "") or data.get("file", "")
        return (
            MessageSegment(type=SegmentType.AUDIO, url=url),
            "",
        )

    if mapped_type == SegmentType.VIDEO:
        url = data.get("url", "") or data.get("file", "")
        return (
            MessageSegment(type=SegmentType.VIDEO, url=url, file_path=data.get("file", "")),
            "",
        )

    if mapped_type == SegmentType.FILE:
        url = data.get("url", "") or data.get("file", "")
        name = data.get("name", "") or data.get("file_name", "")
        return (
            MessageSegment(type=SegmentType.FILE, url=url, file_name=name),
            "",
        )

    if mapped_type == SegmentType.LOCATION:
        lat = data.get("latitude", data.get("lat", ""))
        lon = data.get("longitude", data.get("lon", ""))
        return (
            MessageSegment(type=SegmentType.LOCATION, content=f"{lat},{lon}"),
            f"[location:{lat},{lon}]",
        )

    # 未知类型：降级为 text
    try:
        text = str(raw_seg)
    except Exception:
        text = f"[{seg_type}]"
    if text:
        return MessageSegment(type=SegmentType.TEXT, content=text), text
    return None


def _check_is_to_me(event: Any) -> bool:
    """检查事件是否与 Bot 相关。"""
    try:
        return bool(event.is_tome())
    except Exception:
        pass

    # 私聊默认 to_me
    try:
        session = event.get_session_id()
        if "group" not in session.lower() and "guild" not in session.lower():
            return True
    except Exception:
        pass

    return False


def _extract_timestamp(event: Any) -> float:
    """尝试从事件提取时间戳。"""
    for attr in ("time", "timestamp", "created_at"):
        ts = getattr(event, attr, None)
        if ts is not None:
            try:
                return float(ts)
            except (ValueError, TypeError):
                pass
    return time.time()
