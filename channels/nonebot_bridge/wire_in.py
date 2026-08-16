"""父进程侧事件转换 — 桥接线协议 JSON → AdapterMessage。

纯函数模块：输入 worker 上报的中性事件 dict，输出频道系统统一消息模型，
不依赖 nonebot（主应用已移除 nonebot 依赖，全部转换发生在 worker 侧）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
)

# 线协议段类型 → SegmentType
_WIRE_SEG_MAP: Dict[str, SegmentType] = {
    "text": SegmentType.TEXT,
    "image": SegmentType.IMAGE,
    "at": SegmentType.AT,
    "voice": SegmentType.VOICE,
    "audio": SegmentType.AUDIO,
    "video": SegmentType.VIDEO,
    "file": SegmentType.FILE,
    "location": SegmentType.LOCATION,
}


def wire_event_to_adapter_message(payload: Dict[str, Any]) -> Optional[AdapterMessage]:
    """线协议事件 → AdapterMessage（字段缺失时按缺省降级）。"""
    session = payload.get("session") or {}
    sender = payload.get("sender") or {}

    user_id = str(sender.get("user_id", "") or "")
    if not user_id:
        return None

    session_type = str(session.get("type", "private")) or "private"
    channel_type = ChannelType.GROUP if session_type == "group" else ChannelType.PRIVATE
    channel_id = str(session.get("id", "") or "")
    if channel_type == ChannelType.PRIVATE:
        channel_id = user_id

    segments = _convert_segments(payload.get("segments") or [])

    return AdapterMessage(
        message_id=str(payload.get("message_id", "") or ""),
        sender=AdapterUser(
            platform=str(sender.get("platform", "") or "nb_unknown"),
            user_id=user_id,
            user_name=str(sender.get("user_name", "") or ""),
        ),
        channel=AdapterChannel(
            channel_id=channel_id,
            channel_type=channel_type,
        ),
        content=str(payload.get("text", "") or ""),
        segments=segments,
        is_to_me=bool(payload.get("is_to_me", False)),
        timestamp=_to_float(payload.get("timestamp")) or time.time(),
        reply_to_id=str(payload.get("reply_to", "") or ""),
    )


def _convert_segments(wire_segments: List[Dict[str, Any]]) -> List[MessageSegment]:
    """线协议段列表 → MessageSegment 列表。"""
    segments: List[MessageSegment] = []
    for wire_seg in wire_segments:
        seg_type = _WIRE_SEG_MAP.get(str(wire_seg.get("seg", "")))
        if seg_type is None:
            # 未知段类型：文本化降级
            fallback = str(wire_seg.get("text", "") or "")
            if fallback:
                segments.append(MessageSegment(type=SegmentType.TEXT, content=fallback))
            continue

        if seg_type == SegmentType.TEXT:
            content = str(wire_seg.get("text", ""))
            if content:
                segments.append(MessageSegment(type=SegmentType.TEXT, content=content))
        elif seg_type == SegmentType.AT:
            target = str(wire_seg.get("user_id", "") or "")
            if target:
                at_text = f"[at_uid:{target}]"
                segments.append(
                    MessageSegment(type=SegmentType.AT, at_user_id=target, content=at_text)
                )
        elif seg_type == SegmentType.LOCATION:
            content = str(wire_seg.get("text", "") or "")
            if content:
                segments.append(MessageSegment(type=SegmentType.LOCATION, content=content))
        else:
            # 媒体段：url / file / name 透传（文件段允许仅有名称）
            url = str(wire_seg.get("url", "") or "")
            file_path = str(wire_seg.get("file", "") or "")
            name = str(wire_seg.get("name", "") or "")
            if url or file_path or name:
                segments.append(
                    MessageSegment(
                        type=seg_type,
                        url=url,
                        file_path=file_path,
                        file_name=name,
                    )
                )
    return segments


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
