"""QQ 事件解析器（OneBot v11 协议）。

将 OneBot v11 标准 JSON 事件转换为 ``AdapterMessage``。
参考: https://github.com/botuniverse/onebot-11/tree/master/event
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from core.log import log

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
)


# API 回调类型：接收 action 和 params，返回 API 响应
ApiCaller = Callable[[str, Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]

# 群成员名片缓存：{group_id: {user_id: nickname}}
_group_member_cache: Dict[str, Dict[str, str]] = {}

# 合并转发最大递归深度
MAX_FORWARD_DEPTH = 3


def get_cached_nickname(group_id: str, user_id: str) -> Optional[str]:
    """从缓存中获取群成员昵称。"""
    return _group_member_cache.get(group_id, {}).get(user_id)


def cache_nickname(group_id: str, user_id: str, nickname: str) -> None:
    """缓存群成员昵称。"""
    if group_id not in _group_member_cache:
        _group_member_cache[group_id] = {}
    _group_member_cache[group_id][user_id] = nickname


async def parse_event_async(
    data: Dict[str, Any],
    api_caller: Optional[ApiCaller] = None,
) -> Optional[AdapterMessage]:
    """异步解析 OneBot v11 事件（消息 + 通知）。

    Args:
        data: OneBot v11 事件数据
        api_caller: API 回调函数，用于获取被引用消息、群成员信息等

    Returns:
        解析后的 AdapterMessage，或 None
    """
    post_type = data.get("post_type")
    if post_type == "message":
        return await _parse_message_event_async(data, api_caller)
    if post_type == "notice":
        return await _parse_notice_event(data, api_caller)
    return None


def parse_event(data: Dict[str, Any]) -> Optional[AdapterMessage]:
    """同步解析 OneBot v11 事件（消息 + 通知）。

    注意：此函数为兼容性保留，不支持获取引用消息内容、异步获取昵称和文件 URL。
    建议使用 parse_event_async。
    """
    post_type = data.get("post_type")
    if post_type == "message":
        return _parse_message_event_sync(data)
    if post_type == "notice":
        return _parse_notice_event_sync(data)
    return None


async def _parse_message_event_async(
    data: Dict[str, Any],
    api_caller: Optional[ApiCaller] = None,
) -> AdapterMessage:
    """异步解析 message 类型事件，支持获取引用消息内容和群成员昵称。"""
    message_type = data.get("message_type", "private")
    user_id = str(data.get("user_id", ""))
    message_id = str(data.get("message_id", ""))
    self_id = str(data.get("self_id", ""))

    sender_info = data.get("sender", {})
    user_name = (
        sender_info.get("card")
        or sender_info.get("nickname")
        or user_id
    )

    if message_type == "group":
        group_id = str(data.get("group_id", ""))
        channel = AdapterChannel(
            channel_id=group_id,
            channel_type=ChannelType.GROUP,
        )
        # 缓存发送者的群名片
        if user_name and user_name != user_id:
            cache_nickname(group_id, user_id, user_name)
    else:
        group_id = ""
        channel = AdapterChannel(
            channel_id=user_id,
            channel_type=ChannelType.PRIVATE,
        )

    raw_message = data.get("message", [])

    # 异步解析消息段（支持获取昵称和合并转发）
    content, segments = await _parse_message_segments_async(
        raw_message, group_id, self_id, api_caller
    )

    # 提取引用消息 ID 并获取内容
    reply_to_id = _extract_reply_id(raw_message)
    reply_content = ""
    if reply_to_id and api_caller:
        reply_content = await _fetch_reply_content(reply_to_id, api_caller, group_id, self_id)

    is_to_me = _check_to_me(data)

    return AdapterMessage(
        message_id=message_id,
        sender=AdapterUser(
            platform="qq",
            user_id=user_id,
            user_name=user_name,
        ),
        channel=channel,
        content=content,
        segments=segments,
        is_to_me=is_to_me,
        timestamp=float(data.get("time", time.time())),
        reply_to_id=reply_to_id,
        reply_content=reply_content,
    )


def _parse_message_event_sync(data: Dict[str, Any]) -> AdapterMessage:
    """同步解析 message 类型事件（不支持获取引用内容和异步昵称）。"""
    message_type = data.get("message_type", "private")
    user_id = str(data.get("user_id", ""))
    message_id = str(data.get("message_id", ""))
    self_id = str(data.get("self_id", ""))

    sender_info = data.get("sender", {})
    user_name = (
        sender_info.get("card")
        or sender_info.get("nickname")
        or user_id
    )

    if message_type == "group":
        group_id = str(data.get("group_id", ""))
        channel = AdapterChannel(
            channel_id=group_id,
            channel_type=ChannelType.GROUP,
        )
        if user_name and user_name != user_id:
            cache_nickname(group_id, user_id, user_name)
    else:
        group_id = ""
        channel = AdapterChannel(
            channel_id=user_id,
            channel_type=ChannelType.PRIVATE,
        )

    raw_message = data.get("message", [])
    content, segments = _parse_message_segments_sync(raw_message, group_id, self_id)
    reply_to_id = _extract_reply_id(raw_message)

    is_to_me = _check_to_me(data)

    return AdapterMessage(
        message_id=message_id,
        sender=AdapterUser(
            platform="qq",
            user_id=user_id,
            user_name=user_name,
        ),
        channel=channel,
        content=content,
        segments=segments,
        is_to_me=is_to_me,
        timestamp=float(data.get("time", time.time())),
        reply_to_id=reply_to_id,
    )


async def _parse_message_segments_async(
    raw_message: Any,
    group_id: str = "",
    self_id: str = "",
    api_caller: Optional[ApiCaller] = None,
) -> Tuple[str, List[MessageSegment]]:
    """异步解析消息段列表，返回 (纯文本, 消息段列表)。

    支持异步获取群成员昵称和合并转发内容。
    @ 提及使用统一标签 [at_uid:xxx]。
    """
    if isinstance(raw_message, str):
        return raw_message, [MessageSegment(type=SegmentType.TEXT, content=raw_message)]

    text_parts: List[str] = []
    segments: List[MessageSegment] = []

    if not isinstance(raw_message, list):
        return "", []

    for seg in raw_message:
        seg_type = seg.get("type", "")
        seg_data: Dict[str, Any] = seg.get("data", {})

        if seg_type == "text":
            text = seg_data.get("text", "")
            text_parts.append(text)
            segments.append(MessageSegment(type=SegmentType.TEXT, content=text))

        elif seg_type == "image":
            segments.append(_build_media_segment(SegmentType.IMAGE, seg_data))

        elif seg_type == "at":
            qq = str(seg_data.get("qq", ""))
            at_text = f"[at_uid:{qq}]" if qq else ""
            if at_text:
                nickname = seg_data.get("name", "")
                if nickname and group_id and qq not in ("all", self_id):
                    cache_nickname(group_id, qq, nickname)
                text_parts.append(at_text)
                segments.append(MessageSegment(
                    type=SegmentType.AT,
                    at_user_id=qq,
                    content=at_text,
                ))

        elif seg_type == "record":
            segments.append(_build_media_segment(
                SegmentType.VOICE, seg_data, mime_type="audio/amr"))

        elif seg_type == "video":
            segments.append(_build_media_segment(
                SegmentType.VIDEO, seg_data, mime_type="video/mp4"))

        elif seg_type == "file":
            segments.append(_build_media_segment(SegmentType.FILE, seg_data))

        elif seg_type == "json":
            summary = _parse_json_card(seg_data)
            text_parts.append(summary)
            segments.append(MessageSegment(
                type=SegmentType.JSON_CARD,
                content=summary,
            ))

        elif seg_type == "forward":
            # 尝试解析合并转发内容
            forward_id = seg_data.get("id", "")
            forward_content = await _parse_forward_message(
                forward_id, api_caller, group_id, self_id
            )
            text_parts.append(forward_content)
            segments.append(MessageSegment(
                type=SegmentType.FORWARD,
                content=forward_content,
            ))

        elif seg_type in ("face", "bface"):
            text_parts.append("[表情]")

        elif seg_type in ("mface", "marketface"):
            summary = seg_data.get("summary", "") or seg_data.get("key", "")
            label = f"[动态表情:{summary}]" if summary else "[动态表情]"
            text_parts.append(label)

        elif seg_type == "reply":
            pass

        else:
            text = seg_data.get("text", "")
            if text:
                text_parts.append(text)

    return "".join(text_parts), segments


def _parse_message_segments_sync(
    raw_message: Any,
    group_id: str = "",
    self_id: str = "",
) -> Tuple[str, List[MessageSegment]]:
    """同步解析消息段列表（不支持异步获取昵称和合并转发解析）。"""
    if isinstance(raw_message, str):
        return raw_message, [MessageSegment(type=SegmentType.TEXT, content=raw_message)]

    text_parts: List[str] = []
    segments: List[MessageSegment] = []

    if not isinstance(raw_message, list):
        return "", []

    for seg in raw_message:
        seg_type = seg.get("type", "")
        seg_data: Dict[str, Any] = seg.get("data", {})

        if seg_type == "text":
            text = seg_data.get("text", "")
            text_parts.append(text)
            segments.append(MessageSegment(type=SegmentType.TEXT, content=text))

        elif seg_type == "image":
            segments.append(_build_media_segment(SegmentType.IMAGE, seg_data))

        elif seg_type == "at":
            qq = str(seg_data.get("qq", ""))
            at_text = f"[at_uid:{qq}](me)" if qq else ""
            if at_text:
                text_parts.append(at_text)
                segments.append(MessageSegment(
                    type=SegmentType.AT,
                    at_user_id=qq,
                    content=at_text,
                ))

        elif seg_type == "record":
            segments.append(_build_media_segment(
                SegmentType.VOICE, seg_data, mime_type="audio/amr"))

        elif seg_type == "video":
            segments.append(_build_media_segment(
                SegmentType.VIDEO, seg_data, mime_type="video/mp4"))

        elif seg_type == "file":
            segments.append(_build_media_segment(SegmentType.FILE, seg_data))

        elif seg_type == "json":
            summary = _parse_json_card(seg_data)
            text_parts.append(summary)
            segments.append(MessageSegment(
                type=SegmentType.JSON_CARD,
                content=summary,
            ))

        elif seg_type == "forward":
            text_parts.append("[合并转发消息]")
            segments.append(MessageSegment(
                type=SegmentType.FORWARD,
                content="[合并转发消息]",
            ))

        elif seg_type in ("face", "bface"):
            text_parts.append("[表情]")

        elif seg_type in ("mface", "marketface"):
            summary = seg_data.get("summary", "") or seg_data.get("key", "")
            label = f"[动态表情:{summary}]" if summary else "[动态表情]"
            text_parts.append(label)

        elif seg_type == "reply":
            pass

        else:
            text = seg_data.get("text", "")
            if text:
                text_parts.append(text)

    return "".join(text_parts), segments


# ======================================================================
# 异步 API 辅助函数
# ======================================================================


async def _get_member_nickname(
    group_id: str,
    user_id: str,
    api_caller: Optional[ApiCaller],
    timeout: float = 2.0,
) -> str:
    """获取群成员昵称，优先使用缓存。

    为避免阻塞消息处理，API 调用有超时限制。
    超时或失败时返回空字符串，调用方应使用 uid 作为回退。
    """
    if not group_id or not user_id:
        return ""

    cached = get_cached_nickname(group_id, user_id)
    if cached:
        return cached

    if not api_caller:
        return ""

    try:
        result = await asyncio.wait_for(
            api_caller("get_group_member_info", {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "no_cache": False,
            }),
            timeout=timeout,
        )
        if result and result.get("data"):
            data = result["data"]
            nickname = data.get("card") or data.get("nickname") or ""
            if nickname:
                cache_nickname(group_id, user_id, nickname)
            return nickname
    except asyncio.TimeoutError:
        log(f"获取群成员昵称超时: group={group_id} user={user_id}", "DEBUG", tag="QQ")
    except Exception as exc:
        log(f"获取群成员信息失败: {exc}", "DEBUG", tag="QQ")
    return ""


async def _fetch_reply_content(
    reply_id: str,
    api_caller: Optional[ApiCaller],
    group_id: str = "",
    self_id: str = "",
    timeout: float = 3.0,
) -> str:
    """获取被引用消息的内容。

    为避免阻塞消息处理，API 调用有超时限制。
    """
    if not reply_id or not api_caller:
        return ""

    try:
        result = await asyncio.wait_for(
            api_caller("get_msg", {"message_id": int(reply_id)}),
            timeout=timeout,
        )
        if result and result.get("data"):
            msg_data = result["data"]
            raw_message = msg_data.get("message", [])
            # 使用同步解析避免递归异步调用
            content, _ = _parse_message_segments_sync(raw_message, group_id, self_id)
            sender = msg_data.get("sender", {})
            sender_name = (
                sender.get("card")
                or sender.get("nickname")
                or str(sender.get("user_id", ""))
            )
            # 截断过长内容
            content_preview = content[:200] if len(content) > 200 else content
            return f"{sender_name}: {content_preview}"
    except asyncio.TimeoutError:
        log(f"获取引用消息超时: message_id={reply_id}", "DEBUG", tag="QQ")
    except Exception as exc:
        log(f"获取引用消息失败: {exc}", "DEBUG", tag="QQ")
    return ""


async def _parse_forward_message(
    forward_id: str,
    api_caller: Optional[ApiCaller],
    group_id: str = "",
    self_id: str = "",
    depth: int = 0,
    timeout: float = 5.0,
) -> str:
    """解析合并转发消息内容。

    为避免阻塞消息处理，API 调用有超时限制。
    """
    if depth >= MAX_FORWARD_DEPTH:
        return "[嵌套转发消息，层级过深]"

    if not forward_id or not api_caller:
        return "[合并转发消息]"

    try:
        result = await asyncio.wait_for(
            api_caller("get_forward_msg", {"message_id": forward_id}),
            timeout=timeout,
        )
        if not result or not result.get("data"):
            return "[合并转发消息]"

        messages = result["data"].get("messages", [])
        if not messages:
            return "[合并转发消息]"

        parts: List[str] = ["[合并转发消息]"]
        for msg in messages[:10]:
            sender = msg.get("sender", {})
            name = sender.get("card") or sender.get("nickname") or "未知"
            content_segs = msg.get("content", msg.get("message", []))
            # 使用同步解析
            text, _ = _parse_message_segments_sync(content_segs, group_id, self_id)
            text_preview = text[:100] if len(text) > 100 else text
            if text_preview:
                parts.append(f"  {name}: {text_preview}")

        return "\n".join(parts)
    except asyncio.TimeoutError:
        log(f"解析合并转发超时: forward_id={forward_id}", "DEBUG", tag="QQ")
        return "[合并转发消息]"
    except Exception as exc:
        log(f"解析合并转发失败: {exc}", "DEBUG", tag="QQ")
        return "[合并转发消息]"


def _extract_reply_id(raw_message: Any) -> str:
    """从消息段中提取回复引用的消息 ID。"""
    if not isinstance(raw_message, list):
        return ""
    for seg in raw_message:
        if seg.get("type") == "reply":
            return str(seg.get("data", {}).get("id", ""))
    return ""


def _check_to_me(data: Dict[str, Any]) -> bool:
    """判断消息是否 @bot 或为私聊。"""
    if data.get("message_type") == "private":
        return True

    raw_message = data.get("message", [])
    if not isinstance(raw_message, list):
        return False

    self_id = str(data.get("self_id", ""))
    for seg in raw_message:
        if seg.get("type") == "at":
            qq = str(seg.get("data", {}).get("qq", ""))
            if qq == self_id:
                return True
    return False


# ======================================================================
# JSON 卡片解析
# ======================================================================


def _strip_file_prefix(path: str) -> str:
    """移除 OneBot 文件路径的 file: 前缀。"""
    if path.startswith("file://"):
        # file:///abs/path -> /abs/path（保留根斜杠）
        return path[len("file://"):]
    if path.startswith("file:"):
        return path[len("file:"):]
    return path


def _build_media_segment(
    seg_type: SegmentType,
    seg_data: Dict[str, Any],
    mime_type: str = "",
) -> MessageSegment:
    """从 OneBot 媒体段数据构建 MessageSegment。

    `file` 字段仅当指向真实存在的本地文件时作为 file_path（同机部署零拷贝），
    否则视为文件名填入 file_name，避免产生误导性的假路径。
    file_id / size 一并保留，供 AI 按需调用 qq_download_file 下载。
    """
    raw_file = _strip_file_prefix(seg_data.get("file", "") or "")
    file_path = ""
    file_name = str(seg_data.get("name", "") or "")
    if raw_file:
        if os.path.isfile(raw_file):
            file_path = raw_file
        elif not file_name:
            file_name = raw_file

    raw_size = seg_data.get("size", seg_data.get("file_size", 0))
    try:
        file_size = int(raw_size)
    except (TypeError, ValueError):
        file_size = 0

    return MessageSegment(
        type=seg_type,
        url=str(seg_data.get("url", "") or ""),
        file_path=file_path,
        file_name=file_name,
        file_id=str(seg_data.get("file_id", "") or ""),
        file_size=file_size,
        mime_type=mime_type,
    )


def _get_str_val(detail: Dict[str, Any], key: str) -> Optional[str]:
    """安全从 detail 字典中提取非空字符串。"""
    value = detail.get(key)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _extract_json_card_detail(json_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 JSON 卡片的 meta 中提取第一个有效 detail。"""
    meta = json_data.get("meta")
    if not isinstance(meta, dict):
        return None
    for key in ("detail_1", "detail_2", "detail_3"):
        detail = meta.get(key)
        if isinstance(detail, dict) and any(
            detail.get(k) for k in ("title", "desc", "url", "qqdocurl")
        ):
            return detail
    return None


def _parse_json_card(seg_data: Dict[str, Any]) -> str:
    """解析 JSON 卡片消息段（OneBot json 类型的 data 字典），返回文本摘要。

    格式：[json_card:由xxx转发自yyy的标题为zzz的卡片消息，链接为...]
    """
    try:
        raw = seg_data.get("data", "")
        if not raw:
            return "[JSON卡片]"
        json_data: Dict[str, Any] = json.loads(raw) if isinstance(raw, str) else raw

        detail = _extract_json_card_detail(json_data)

        if detail is None:
            # fallback：使用 prompt 字段
            prompt = json_data.get("prompt", "")
            if isinstance(prompt, str) and prompt.strip():
                clean = prompt.removeprefix("[QQ小程序]").strip()
                return f"[json_card:[卡片消息]{clean[:100]}]" if clean else "[json_card:[JSON卡片]]"
            return "[json_card:[JSON卡片]]"

        app_title = _get_str_val(detail, "title")
        card_desc = _get_str_val(detail, "desc")
        url_val = detail.get("qqdocurl") or detail.get("url")
        url: Optional[str] = url_val if isinstance(url_val, str) else None
        host = detail.get("host")
        host_nick_raw = host.get("nick") if isinstance(host, dict) else None
        host_nick: Optional[str] = host_nick_raw if isinstance(host_nick_raw, str) and host_nick_raw.strip() else None

        parts: List[str] = ["[卡片消息]"]
        if host_nick:
            parts.append(f"由{host_nick}")
        if app_title:
            parts.append(f"转发自{app_title}的" if host_nick else f"来自{app_title}的")
        if card_desc:
            desc = card_desc
            if desc.startswith('"') and desc.endswith('"'):
                desc = desc[1:-1]
            parts.append(f'标题为"{desc}"的')
        parts.append("卡片消息")
        if url:
            display = url if len(url) <= 50 else f"{url[:50]}..."
            parts.append(f"，链接为{display}")

        summary = "".join(parts)
        return f"[json_card:{summary}]"

    except (json.JSONDecodeError, Exception):
        return "[json_card:[JSON卡片]]"


# ======================================================================
# 通知事件解析
# ======================================================================


def _parse_notice_event_sync(data: Dict[str, Any]) -> Optional[AdapterMessage]:
    """同步解析通知事件（兼容性保留，不支持获取文件 URL）。"""
    notice_text, is_to_me = _format_notice_sync(data)
    if not notice_text:
        return None

    user_id = str(data.get("user_id", "system"))
    group_id = data.get("group_id")

    if group_id:
        channel = AdapterChannel(
            channel_id=str(group_id),
            channel_type=ChannelType.GROUP,
        )
    else:
        channel = AdapterChannel(
            channel_id=user_id,
            channel_type=ChannelType.PRIVATE,
        )

    return AdapterMessage(
        sender=AdapterUser(
            platform="qq",
            user_id=user_id,
            user_name="",
        ),
        channel=channel,
        content=notice_text,
        segments=[MessageSegment(type=SegmentType.TEXT, content=notice_text)],
        is_to_me=is_to_me,
        timestamp=float(data.get("time", time.time())),
    )


def _format_notice_sync(data: Dict[str, Any]) -> Tuple[str, bool]:
    """同步版本的通知格式化（不支持文件 URL 获取）。"""
    notice_type = data.get("notice_type", "")
    sub_type = data.get("sub_type", "")
    user_id = data.get("user_id", "")
    operator_id = data.get("operator_id", "")
    self_id = str(data.get("self_id", ""))

    if notice_type == "notify" and sub_type == "poke":
        target_id = str(data.get("target_id", ""))
        raw_info = data.get("raw_info", [])
        item2 = raw_info[2] if len(raw_info) > 2 else None
        item4 = raw_info[4] if len(raw_info) > 4 else None
        poke_style = item2.get("txt", "戳一戳") if isinstance(item2, dict) else "戳一戳"
        poke_suffix = item4.get("txt", "") if isinstance(item4, dict) else ""

        group_id = str(data.get("group_id", ""))
        sender_nickname = get_cached_nickname(group_id, str(user_id)) if group_id else ""
        sender_display = sender_nickname or f"用户{user_id}"

        is_poke_me = target_id == self_id
        if is_poke_me:
            text = f"({sender_display} {poke_style} 你 {poke_suffix})".strip()
        else:
            target_nickname = get_cached_nickname(group_id, target_id) if group_id else ""
            target_display = target_nickname or f"用户{target_id}"
            text = f"({sender_display} {poke_style} {target_display} {poke_suffix})".strip()
        return text, is_poke_me

    if notice_type == "group_upload":
        file_info = data.get("file", {})
        file_name = file_info.get("name", "未知文件")
        file_size = file_info.get("size", 0)
        file_id = str(file_info.get("id", "") or "")
        hint = f"，file_id: {file_id}，可用 qq_download_file 下载到本地" if file_id else ""
        return (
            f"(用户 {user_id} 上传了文件: {file_name}, "
            f"大小: {_format_file_size(file_size)}{hint})"
        ), False

    if notice_type == "group_increase":
        return f"(新成员 {user_id} 加入了群聊)", False

    if notice_type == "group_decrease":
        return f"(成员 {user_id} 离开了群聊)", False

    if notice_type == "group_ban":
        duration = data.get("duration", 0)
        if duration == 0:
            return f"(成员 {user_id} 被 {operator_id} 解除禁言)", False
        return f"(成员 {user_id} 被 {operator_id} 禁言 {_format_duration(duration)})", False

    if notice_type == "group_recall":
        if str(user_id) == str(operator_id):
            return f"(成员 {user_id} 撤回了一条消息)", False
        return f"(成员 {user_id} 的消息被 {operator_id} 撤回)", False

    if notice_type == "group_admin":
        action = "被设为管理员" if sub_type == "set" else "被取消管理员"
        return f"(成员 {user_id} {action})", False

    if notice_type == "friend_add":
        return f"(用户 {user_id} 已成为好友)", True

    return "", False


async def _parse_notice_event(
    data: Dict[str, Any],
    api_caller: Optional[ApiCaller] = None,
) -> Optional[AdapterMessage]:
    """解析通知事件（群成员变动、戳一戳、禁言、撤回等）。"""
    notice_text, is_to_me = await _format_notice(data, api_caller)
    if not notice_text:
        return None

    user_id = str(data.get("user_id", "system"))
    group_id = data.get("group_id")

    if group_id:
        channel = AdapterChannel(
            channel_id=str(group_id),
            channel_type=ChannelType.GROUP,
        )
    else:
        channel = AdapterChannel(
            channel_id=user_id,
            channel_type=ChannelType.PRIVATE,
        )

    return AdapterMessage(
        sender=AdapterUser(
            platform="qq",
            user_id=user_id,
            user_name="",
        ),
        channel=channel,
        content=notice_text,
        segments=[MessageSegment(type=SegmentType.TEXT, content=notice_text)],
        is_to_me=is_to_me,
        timestamp=float(data.get("time", time.time())),
    )


async def _format_notice(
    data: Dict[str, Any],
    api_caller: Optional[ApiCaller] = None,
) -> Tuple[str, bool]:
    """将通知事件格式化为可读文本。

    Returns:
        (notice_text, is_to_me): 格式化的通知文本和是否针对机器人
    """
    notice_type = data.get("notice_type", "")
    sub_type = data.get("sub_type", "")
    user_id = data.get("user_id", "")
    operator_id = data.get("operator_id", "")
    self_id = str(data.get("self_id", ""))

    if notice_type == "notify" and sub_type == "poke":
        target_id = str(data.get("target_id", ""))
        raw_info = data.get("raw_info", [])
        item2 = raw_info[2] if len(raw_info) > 2 else None
        item4 = raw_info[4] if len(raw_info) > 4 else None
        poke_style = item2.get("txt", "戳一戳") if isinstance(item2, dict) else "戳一戳"
        poke_suffix = item4.get("txt", "") if isinstance(item4, dict) else ""

        # 解析戳一戳动作图片 URL
        action_img = ""
        if len(raw_info) > 1 and isinstance(raw_info[1], dict) and raw_info[1].get("type") == "img":
            action_img = raw_info[1].get("src", "")

        group_id = str(data.get("group_id", ""))
        sender_nickname = get_cached_nickname(group_id, str(user_id)) if group_id else ""
        sender_display = sender_nickname or f"用户{user_id}"

        is_poke_me = target_id == self_id
        if is_poke_me:
            text = f"({sender_display} {poke_style} 你 {poke_suffix})".strip()
        else:
            target_nickname = get_cached_nickname(group_id, target_id) if group_id else ""
            target_display = target_nickname or f"用户{target_id}"
            text = f"({sender_display} {poke_style} {target_display} {poke_suffix})".strip()

        if action_img:
            text = f"{text} [动作图片:{action_img}]"
        return text, is_poke_me

    if notice_type == "group_upload":
        file_info = data.get("file", {})
        file_name = file_info.get("name", "未知文件")
        file_size = file_info.get("size", 0)
        file_url = file_info.get("url", "")
        file_id = str(file_info.get("id", "") or "")

        # 尝试通过 get_file API 获取下载 URL
        if not file_url and api_caller and file_id:
            file_url = await _fetch_file_url(file_id, api_caller)

        size_display = _format_file_size(file_size)
        text = f"(用户 {user_id} 上传了文件: {file_name}, 大小: {size_display}"
        if file_url:
            text += f", 链接: {file_url}，可用 web_download 下载"
        elif file_id:
            text += f", file_id: {file_id}，可用 qq_download_file 下载到本地"
        text += ")"
        return text, False

    if notice_type == "group_increase":
        return f"(新成员 {user_id} 加入了群聊)", False

    if notice_type == "group_decrease":
        return f"(成员 {user_id} 离开了群聊)", False

    if notice_type == "group_ban":
        duration = data.get("duration", 0)
        if duration == 0:
            return f"(成员 {user_id} 被 {operator_id} 解除禁言)", False
        return f"(成员 {user_id} 被 {operator_id} 禁言 {_format_duration(duration)})", False

    if notice_type == "group_recall":
        if str(user_id) == str(operator_id):
            return f"(成员 {user_id} 撤回了一条消息)", False
        return f"(成员 {user_id} 的消息被 {operator_id} 撤回)", False

    if notice_type == "group_admin":
        action = "被设为管理员" if sub_type == "set" else "被取消管理员"
        return f"(成员 {user_id} {action})", False

    if notice_type == "friend_add":
        return f"(用户 {user_id} 已成为好友)", True

    return "", False


async def _fetch_file_url(
    file_id: str,
    api_caller: ApiCaller,
    timeout: float = 3.0,
) -> str:
    """通过 get_file API 获取文件下载 URL。"""
    try:
        result = await asyncio.wait_for(
            api_caller("get_file", {"file_id": file_id}),
            timeout=timeout,
        )
        if result and result.get("data"):
            return result["data"].get("url", "") or result["data"].get("file", "")
    except asyncio.TimeoutError:
        log(f"获取文件 URL 超时: file_id={file_id}", "DEBUG", tag="QQ")
    except Exception as exc:
        log(f"获取文件 URL 失败: {exc}", "DEBUG", tag="QQ")
    return ""


def _format_file_size(size_bytes: int) -> str:
    """将字节数格式化为可读的文件大小。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _format_duration(seconds: int) -> str:
    """将秒数格式化为可读的时长。"""
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    if seconds < 86400:
        h, m = divmod(seconds, 3600)
        return f"{h}小时{m // 60}分钟" if m >= 60 else f"{h}小时"
    d, remainder = divmod(seconds, 86400)
    h = remainder // 3600
    return f"{d}天{h}小时" if h else f"{d}天"
