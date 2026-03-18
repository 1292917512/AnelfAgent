"""飞书入站消息处理 -- 事件解析、消息构建、媒体下载。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

import lark_oapi as lark

from core.log import log

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
)
from .helpers import (
    check_bot_mentioned,
    extract_media_key,
    parse_mentions_from_event,
    parse_message_content,
    parse_post_content,
)
from .media import download_image, download_message_resource
from .types import FeishuMention

if TYPE_CHECKING:
    pass


# ------------------------------------------------------------------
# 消息去重（LRU）
# ------------------------------------------------------------------

_DEDUP_MAX = 2000
_seen_message_ids: OrderedDict[str, float] = OrderedDict()


def _is_duplicate(message_id: str) -> bool:
    """基于 message_id 的内存去重。"""
    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = time.time()
    while len(_seen_message_ids) > _DEDUP_MAX:
        _seen_message_ids.popitem(last=False)
    return False


# ------------------------------------------------------------------
# 事件处理入口（供 adapter.py 注册到 EventDispatcherHandler）
# ------------------------------------------------------------------


OnMessageCallback = Callable[[AdapterMessage], Awaitable[None]]


def build_message_handler(
    *,
    client: lark.Client,
    bot_open_id: str,
    require_mention: bool,
    on_message: OnMessageCallback,
    main_loop: Optional[asyncio.AbstractEventLoop] = None,
) -> Callable[[lark.im.v1.P2ImMessageReceiveV1], None]:
    """构造飞书消息事件处理函数。

    返回一个同步回调（lark-oapi EventDispatcher 要求同步）。
    消息处理协程必须调度到主事件循环（main_loop），而非 WS 线程的 loop，
    以确保 on_message → dispatch_inbound → Mind 在同一循环上运行。
    """

    def handler(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        log("飞书: SDK 事件回调触发", "DEBUG")
        coro = _handle_message_event(
            data, client=client,
            bot_open_id=bot_open_id,
            require_mention=require_mention,
            on_message=on_message,
        )
        if main_loop is not None and main_loop.is_running():
            log("飞书: 调度到主事件循环", "DEBUG")
            asyncio.run_coroutine_threadsafe(coro, main_loop)
        else:
            log(f"飞书: main_loop 不可用(is_running={main_loop.is_running() if main_loop else None})，fallback asyncio.run", "DEBUG")
            asyncio.run(coro)

    return handler


# ------------------------------------------------------------------
# 异步消息处理
# ------------------------------------------------------------------


async def _handle_message_event(
    data: lark.im.v1.P2ImMessageReceiveV1,
    *,
    client: lark.Client,
    bot_open_id: str,
    require_mention: bool,
    on_message: OnMessageCallback,
) -> None:
    """处理 im.message.receive_v1 事件。"""
    event = data.event
    if not event or not event.message:
        return

    message = event.message
    sender = event.sender
    message_id = message.message_id or ""

    # 去重
    if _is_duplicate(message_id):
        log(f"飞书: 跳过重复消息 {message_id}", "DEBUG")
        return

    # 忽略 Bot 自己发的消息
    sender_open_id = ""
    if sender and sender.sender_id:
        sender_open_id = sender.sender_id.open_id or ""
    if sender_open_id == bot_open_id:
        return

    msg_type = message.message_type or "text"
    raw_content = message.content or ""
    chat_id = message.chat_id or ""
    chat_type = message.chat_type or "p2p"
    parent_id = message.parent_id or ""
    root_id = message.root_id or ""

    # 解析 mentions
    raw_mentions = getattr(message, "mentions", None)
    mentions: List[FeishuMention] = []
    if raw_mentions:
        mentions = _parse_sdk_mentions(raw_mentions)

    # @Bot 检测
    is_to_me = chat_type == "p2p"  # 私聊始终为 True
    if not is_to_me:
        is_to_me = check_bot_mentioned(mentions, bot_open_id)

    # 群聊 require_mention 过滤
    is_group = chat_type == "group"
    trigger_mind = True
    if is_group and require_mention and not is_to_me:
        trigger_mind = False

    # 解析文本内容
    text_content = parse_message_content(raw_content, msg_type)

    if mentions:
        for m in mentions:
            at_tag = f"[at_uid:{m.id.open_id}]"
            if msg_type == "text" and m.key:
                text_content = text_content.replace(m.key, at_tag)
            elif msg_type == "post" and m.id.open_id and m.name:
                text_content = text_content.replace(f"@{m.name}", at_tag)
        text_content = text_content.strip()

    # 提取媒体
    segments: List[MessageSegment] = []
    await _extract_media(
        client=client,
        message_id=message_id,
        msg_type=msg_type,
        raw_content=raw_content,
        segments=segments,
    )

    # 发送者信息
    sender_name = ""
    sender_user_id = sender_open_id
    if sender and sender.sender_id:
        sender_user_id = sender.sender_id.user_id or sender_open_id

    # 构建 AdapterMessage
    adapter_msg = AdapterMessage(
        message_id=message_id,
        sender=AdapterUser(
            platform="feishu",
            user_id=sender_open_id,
            user_name=sender_name or sender_open_id,
        ),
        channel=AdapterChannel(
            channel_id=chat_id,
            channel_type=ChannelType.PRIVATE if chat_type == "p2p" else ChannelType.GROUP,
        ),
        content=text_content,
        segments=segments,
        is_to_me=is_to_me,
        trigger_mind=trigger_mind,
        reply_to_id=parent_id,
    )

    log(f"飞书: 分发消息 chat={chat_id} sender={sender_open_id} type={msg_type} content={text_content[:100]}")
    await on_message(adapter_msg)


# ------------------------------------------------------------------
# 媒体提取
# ------------------------------------------------------------------


async def _extract_media(
    *,
    client: lark.Client,
    message_id: str,
    msg_type: str,
    raw_content: str,
    segments: List[MessageSegment],
) -> None:
    """从消息中提取并下载媒体文件，追加到 segments。"""

    # 富文本中的嵌入图片
    if msg_type == "post":
        result = parse_post_content(raw_content)
        for image_key in result.image_keys:
            info = await download_image(client, message_id, image_key)
            if info:
                segments.append(MessageSegment(
                    type=SegmentType.IMAGE,
                    file_path=info.path,
                    url=info.path,
                    file_name=info.file_name,
                ))
        return

    # 独立媒体消息
    media_types = {"image", "file", "audio", "video", "media", "sticker"}
    if msg_type not in media_types:
        return

    keys = extract_media_key(raw_content, msg_type)
    image_key = keys.get("image_key", "")
    file_key = keys.get("file_key", "")
    file_name = keys.get("file_name", "")
    resource_key = file_key or image_key
    if not resource_key:
        return

    seg_type_map: Dict[str, SegmentType] = {
        "image": SegmentType.IMAGE,
        "file": SegmentType.FILE,
        "audio": SegmentType.AUDIO,
        "video": SegmentType.VIDEO,
        "media": SegmentType.VIDEO,
        "sticker": SegmentType.IMAGE,
    }
    seg_type = seg_type_map.get(msg_type, SegmentType.FILE)
    resource_type = "image" if msg_type == "image" else "file"

    try:
        info = await download_message_resource(
            client, message_id, resource_key,
            resource_type=resource_type,
            msg_type=msg_type,
            file_name=file_name,
        )
        segments.append(MessageSegment(
            type=seg_type,
            file_path=info.path,
            url=info.path,
            file_name=info.file_name or file_name,
        ))
    except Exception as exc:
        log(f"飞书: 媒体下载失败 ({msg_type}, {resource_key}): {exc}", "WARNING")


# ------------------------------------------------------------------
# SDK mentions 解析
# ------------------------------------------------------------------


def _parse_sdk_mentions(raw_mentions: Any) -> List[FeishuMention]:
    """将 lark-oapi SDK 的 mentions 对象列表转为类型化对象。"""
    from .types import FeishuMention, FeishuSenderId

    result: List[FeishuMention] = []
    if not raw_mentions:
        return result

    for m in raw_mentions:
        sid = getattr(m, "id", None)
        open_id = ""
        user_id = ""
        union_id = ""
        if sid:
            open_id = getattr(sid, "open_id", "") or ""
            user_id = getattr(sid, "user_id", "") or ""
            union_id = getattr(sid, "union_id", "") or ""

        result.append(FeishuMention(
            key=getattr(m, "key", "") or "",
            name=getattr(m, "name", "") or "",
            id=FeishuSenderId(open_id=open_id, user_id=user_id, union_id=union_id),
            tenant_key=getattr(m, "tenant_key", "") or "",
        ))
    return result
