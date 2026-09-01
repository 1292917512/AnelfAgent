"""AcFun 直播信号解释器 — ZtLiveSc 信号载荷 → 类型化事件模型。

字段号严格对齐 acfunsdk-ws 携带的 zt.live.interactive .proto 定义；
未识别的信号返回 None（连接层按 debug 计数跳过，不打断流）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .protocol import PbField, pb_get_bytes, pb_get_str, pb_get_varint, pb_parse

# 事件类型词汇表
EVT_COMMENT = "comment"            # 弹幕
EVT_LIKE = "like"                  # 点赞
EVT_ENTER = "enter"                # 进入直播间
EVT_FOLLOW = "follow"              # 关注主播
EVT_GIFT = "gift"                  # 送礼（礼物 ID，名称由管理器解析）
EVT_BANANA = "banana"              # AcFun 投蕉
EVT_JOIN_CLUB = "join_club"        # 加入粉丝团
EVT_DISPLAY = "display"            # 房间实时计数状态
EVT_KICKED = "kicked"              # 被踢出
EVT_VIOLATION = "violation"        # 违规警告
EVT_STATUS_CHANGED = "status_changed"  # 直播状态变更（关播/新直播/封禁）
EVT_TICKET_INVALID = "ticket_invalid"  # 进房票失效（需换票重进）


@dataclass
class LiveEvent:
    """直播间事件（信号载荷的规范化表示）。"""

    kind: str
    user_id: str = ""
    user_name: str = ""
    text: str = ""
    ts_ms: int = 0
    extra: dict = field(default_factory=dict)


def _parse_user(raw: bytes) -> tuple[str, str]:
    """ZtLiveUserInfo（userId=1 nickname=2）。"""
    fields = pb_parse(raw)
    return str(pb_get_varint(fields, 1)), pb_get_str(fields, 2)


def _parse_ac_user(raw: bytes) -> tuple[str, str]:
    """AcFunUserInfo（userId=1 name=2）。"""
    fields = pb_parse(raw)
    return str(pb_get_varint(fields, 1)), pb_get_str(fields, 2)


def _raw_of(fields: List[PbField], num: int) -> Optional[bytes]:
    """取指定字段号的原始字节。"""
    return pb_get_bytes(fields, num)


def interpret(signal_type: str, payload: bytes) -> Optional[LiveEvent]:
    """按信号类型解释载荷；未识别返回 None。"""
    if signal_type == "CommonActionSignalComment":
        # content=1 sendTimeMs=2 userInfo=3
        fields = pb_parse(payload)
        user_raw = _raw_of(fields, 3)
        user_id, user_name = _parse_user(user_raw) if user_raw else ("", "")
        return LiveEvent(
            kind=EVT_COMMENT,
            user_id=user_id,
            user_name=user_name,
            text=pb_get_str(fields, 1),
            ts_ms=pb_get_varint(fields, 2),
        )
    if signal_type in ("CommonActionSignalLike", "CommonActionSignalUserEnterRoom",
                       "CommonActionSignalUserFollowAuthor"):
        # userInfo=1 sendTimeMs=2（同一布局的三个动作信号）
        fields = pb_parse(payload)
        user_raw = _raw_of(fields, 1)
        user_id, user_name = _parse_user(user_raw) if user_raw else ("", "")
        kind = {
            "CommonActionSignalLike": EVT_LIKE,
            "CommonActionSignalUserEnterRoom": EVT_ENTER,
            "CommonActionSignalUserFollowAuthor": EVT_FOLLOW,
        }[signal_type]
        return LiveEvent(kind=kind, user_id=user_id, user_name=user_name,
                         ts_ms=pb_get_varint(fields, 2))
    if signal_type == "CommonActionSignalGift":
        # userInfo=1 sendTimeMs=2 giftId=3 batchSize=4 comboCount=5 comboKey=7
        fields = pb_parse(payload)
        user_raw = _raw_of(fields, 1)
        user_id, user_name = _parse_user(user_raw) if user_raw else ("", "")
        return LiveEvent(
            kind=EVT_GIFT,
            user_id=user_id,
            user_name=user_name,
            ts_ms=pb_get_varint(fields, 2),
            extra={
                "gift_id": str(pb_get_varint(fields, 3)),
                "count": pb_get_varint(fields, 4, 1),
                "combo": pb_get_varint(fields, 5, 1),
            },
        )
    if signal_type == "AcfunActionSignalThrowBanana":
        # visitor=1 count=2 sendTimeMs=3
        fields = pb_parse(payload)
        visitor_raw = _raw_of(fields, 1)
        user_id, user_name = _parse_ac_user(visitor_raw) if visitor_raw else ("", "")
        return LiveEvent(kind=EVT_BANANA, user_id=user_id, user_name=user_name,
                         ts_ms=pb_get_varint(fields, 3),
                         extra={"count": pb_get_varint(fields, 2, 1)})
    if signal_type == "AcfunActionSignalJoinClub":
        # fansInfo=1 uperInfo=2 joinTimeMs=3
        fields = pb_parse(payload)
        fans_raw = _raw_of(fields, 1)
        user_id, user_name = _parse_ac_user(fans_raw) if fans_raw else ("", "")
        return LiveEvent(kind=EVT_JOIN_CLUB, user_id=user_id, user_name=user_name,
                         ts_ms=pb_get_varint(fields, 3))
    if signal_type == "CommonStateSignalDisplayInfo":
        # watchingCount=1 likeCount=2 likeDelta=3（计数为十进制字符串）
        fields = pb_parse(payload)
        return LiveEvent(
            kind=EVT_DISPLAY,
            extra={
                "watching": pb_get_str(fields, 1, "0"),
                "likes": pb_get_str(fields, 2, "0"),
                "banana": "",
            },
        )
    if signal_type == "AcfunStateSignalDisplayInfo":
        # bananaCount=1
        fields = pb_parse(payload)
        return LiveEvent(kind=EVT_DISPLAY, extra={
            "watching": "", "likes": "", "banana": pb_get_str(fields, 1, "0"),
        })
    if signal_type == "ZtLiveScStatusChanged":
        # type=1（LIVE_CLOSED=1 NEW_LIVE_OPENED=2 LIVE_URL_CHANGED=3 LIVE_BANNED=4）bannedInfo=3
        fields = pb_parse(payload)
        change_type = pb_get_varint(fields, 1)
        ban_raw = _raw_of(fields, 3)
        ban_reason = pb_get_str(pb_parse(ban_raw), 1) if ban_raw else ""
        return LiveEvent(kind=EVT_STATUS_CHANGED, extra={
            "change": {1: "live_closed", 2: "new_live_opened", 3: "live_url_changed",
                       4: "live_banned"}.get(change_type, "unknown"),
            "reason": ban_reason,
        })
    if signal_type == "ZtLiveScTicketInvalid":
        return LiveEvent(kind=EVT_TICKET_INVALID)
    if signal_type == "CommonNotifySignalKickedOut":
        fields = pb_parse(payload)
        return LiveEvent(kind=EVT_KICKED, text=pb_get_str(fields, 1))
    if signal_type == "CommonNotifySignalViolationAlert":
        fields = pb_parse(payload)
        return LiveEvent(kind=EVT_VIOLATION, text=pb_get_str(fields, 1))
    return None
