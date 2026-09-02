"""AcFun 入站解析 — 通知中心载荷 → AdapterMessage。

会话 ID 约定（出站路由与思维回复路由共用）：
- ``comment:{rtype}:{rid}`` — 内容评论区（rtype: 1 番剧/2 视频/3 文章/10 动态），多人语义按 GROUP 处理
- ``live:{uid}`` — 直播间（出站弹幕）
- ``notification`` — 通知中心（回复/@/点赞/投蕉推送聚合，kind=notification，不可直接回复）
- ``system`` — 系统通知聚合会话（kind=system，只记录，不可回复）
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageKind,
    notification_channel,
    notification_sender,
)

PLATFORM = "acfun"
PLATFORM_NAME = "AcFun"
SYSTEM_CHANNEL_ID = "system"

# 内容 URL → (rtype, rid)。对齐 AcSource.routes：/v/ac* /a/ac* /bangumi/aa* /moment/am*
_CONTENT_URL_PATTERNS: Tuple[Tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"acfun\.cn/v/ac(\d+)"), 2),
    (re.compile(r"acfun\.cn/a/ac(\d+)"), 3),
    (re.compile(r"acfun\.cn/bangumi/aa(\d+)"), 1),
    (re.compile(r"acfun\.cn/moment/am(\d+)"), 10),
)


def parse_content_url(url: str) -> Optional[Tuple[int, str]]:
    """从内容 URL 解析 (rtype, rid)；无法识别返回 None。"""
    if not url:
        return None
    for pattern, rtype in _CONTENT_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return rtype, match.group(1)
    return None


def comment_chat_id(rtype: Any, rid: Any) -> str:
    return f"comment:{rtype}:{rid}"


def dedup_key(kind: str, item: Dict[str, Any]) -> str:
    """通知去重键：评论类用评论 ID，其余用内容指纹。"""
    ncid = item.get("ncid")
    if ncid:
        return f"{kind}:{ncid}:{item.get('uid', '')}"
    raw = f"{kind}|{item.get('uid', '')}|{item.get('content_url', '')}|{item.get('create_at', '')}|{item.get('intro', '')}"
    return f"{kind}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _sender(item: Dict[str, Any]) -> AdapterUser:
    return AdapterUser(
        platform=PLATFORM,
        user_id=str(item.get("uid") or "unknown"),
        user_name=str(item.get("username") or "unknown"),
    )


def _comment_target_hint(item: Dict[str, Any]) -> str:
    """通知指向的评论目标提示（rtype/rid/ncid），供 acfun_send_comment 工具使用。"""
    target = parse_content_url(str(item.get("content_url") or ""))
    if target is None:
        return ""
    rtype, rid = target
    ncid = str(item.get("ncid") or "")
    hint = f"comment:{rtype}:{rid}"
    if ncid:
        hint += f"，reply_id={ncid}"
    return hint


def notification_to_message(
    kind: str,
    item: Dict[str, Any],
    *,
    like_trigger_mind: bool = False,
    gift_trigger_mind: bool = True,
) -> Optional[AdapterMessage]:
    """把一条通知载荷转成 AdapterMessage；无法识别/无意义的条目返回 None。

    reply/at/like/gift 路由到通知中心会话（kind=notification）：通知是平台推送
    而非对方主动发起的对话，正文携带行为人与可操作参数（评论目标/reply_id），
    AI 要回应用 acfun_send_comment 等工具，而不是直接回复该会话。
    notice/system 保留 system 会话（kind=system，仅记录）。
    """
    actor = _sender(item)
    target_hint = _comment_target_hint(item)

    if kind == "reply":
        content = str(item.get("content") or "").strip()
        if not content:
            return None
        text = f"[回复了你] {actor.user_name}(uid={actor.user_id}): {content}"
        if target_hint:
            text += f"\n（回应方式：acfun_send_comment 目标 {target_hint}）"
        return AdapterMessage(
            sender=notification_sender(PLATFORM, PLATFORM_NAME),
            channel=notification_channel(PLATFORM_NAME),
            content=text,
            kind=MessageKind.NOTIFICATION,
            is_to_me=True,
            trigger_mind=True,
            reply_to_id=str(item.get("ncid") or ""),
            reply_content=str(item.get("replied") or ""),
        )
    if kind == "at":
        intro = str(item.get("intro") or "").strip() or "在评论中提到了你"
        text = f"[@了你] {actor.user_name}(uid={actor.user_id}): {intro}"
        if target_hint:
            text += f"\n（回应方式：acfun_send_comment 目标 {target_hint}）"
        return AdapterMessage(
            sender=notification_sender(PLATFORM, PLATFORM_NAME),
            channel=notification_channel(PLATFORM_NAME),
            content=text,
            kind=MessageKind.NOTIFICATION,
            is_to_me=True,
            trigger_mind=True,
            reply_to_id=str(item.get("ncid") or ""),
        )
    if kind == "like":
        replied = str(item.get("replied") or "").strip()
        detail = f"赞了你的评论：{replied}" if replied else "赞了你的内容"
        return AdapterMessage(
            sender=notification_sender(PLATFORM, PLATFORM_NAME),
            channel=notification_channel(PLATFORM_NAME),
            content=f"[赞了你] {actor.user_name}(uid={actor.user_id}) {detail}",
            kind=MessageKind.NOTIFICATION,
            is_to_me=False,
            trigger_mind=like_trigger_mind,
        )
    if kind == "gift":
        banana = item.get("banana", 0)
        title = str(item.get("content_title") or "").strip()
        detail = f"给你投了 {banana} 根香蕉" + (f"（《{title}》）" if title and title != "动态" else "")
        return AdapterMessage(
            sender=notification_sender(PLATFORM, PLATFORM_NAME),
            channel=notification_channel(PLATFORM_NAME),
            content=f"[投蕉] {actor.user_name}(uid={actor.user_id}) {detail}",
            kind=MessageKind.NOTIFICATION,
            is_to_me=False,
            trigger_mind=gift_trigger_mind,
        )
    if kind in ("notice", "system"):
        intro = str(item.get("intro") or "").strip()
        title = str(item.get("content_title") or "").strip()
        content = f"[系统通知] {title} {intro}".strip()
        if not intro and not title:
            return None
        return AdapterMessage(
            sender=AdapterUser(platform=PLATFORM, user_id="acfun_system", user_name="AcFun 系统"),
            channel=AdapterChannel(channel_id=SYSTEM_CHANNEL_ID, channel_type=ChannelType.PRIVATE, channel_name="系统通知"),
            content=content,
            kind=MessageKind.SYSTEM,
            is_to_me=False,
            trigger_mind=False,
        )
    return None
