"""wcauto 入站解析 — 桥 message 事件 dict → AdapterMessage。

职责边界：本模块只做纯数据转换（群/私判定、自己消息过滤、@我识别、
引用提取、媒体占位），不做网络调用；媒体自动下载由 adapter 在解析后
按配置补充（经桥 RPC），保持解析纯函数可测。

桥事件 data 字段（bridge/backend.py 产出）：
  username / chat_name / is_group / local_id / shard / type(中文类型名) /
  sender_id / sender_username / sender_name / content / create_time / sort_seq
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageKind,
    MessageSegment,
    SegmentType,
)

PLATFORM = "wcauto"

# 微信消息类型名 → 段类型与占位文案
_MEDIA_TYPES: Dict[str, Tuple[SegmentType, str]] = {
    "图片": (SegmentType.IMAGE, "[图片]"),
    "语音": (SegmentType.VOICE, "[语音]"),
    "视频": (SegmentType.VIDEO, "[视频]"),
    "文件": (SegmentType.FILE, "[文件]"),
    "位置": (SegmentType.LOCATION, "[位置]"),
}

# 引用消息正文模式（DB 层无结构化引用字段，从正文提取）：
# 「引用 昵称 的消息：...」形态（引号兼容 「」与"），命中后正文去掉引用块
_QUOTE_RE = re.compile(
    r"^引用\s+(.{1,32}?)\s+的消息[：:]\s*[「\"]?(.+?)[」\"]?\s*\n", re.DOTALL)


def is_self_message(data: Dict[str, Any], self_wxid: str) -> bool:
    """判断是否自己发出的消息（含 AI 经桥发出的回显，必须丢弃防自循环）。"""
    if self_wxid and str(data.get("sender_username") or "") == self_wxid:
        return True
    return str(data.get("sender_id") or "") == "2"


def detect_at_me(content: str, names: List[str]) -> bool:
    """群文本「@昵称」检测（微信 DB 不存 atuserlist，只能正文匹配）。"""
    for name in names:
        name = name.strip()
        if name and f"@{name}" in content:
            return True
    return False


def extract_quote(content: str) -> Tuple[str, str]:
    """提取引用块，返回 (正文, 引用摘要)；无引用时引用摘要为空串。"""
    m = _QUOTE_RE.match(content or "")
    if not m:
        return content, ""
    quoted = f"{m.group(1)}: {m.group(2).strip()}"[:200]
    body = content[m.end():].lstrip("\n")
    return body or content, quoted


def collect_self_names(config_names: str, bridge_nickname: str) -> List[str]:
    """汇总「我」的全部称呼：桥上报昵称 + 配置补充名。"""
    names = [n.strip() for n in (config_names or "").split(",") if n.strip()]
    if bridge_nickname and bridge_nickname not in names:
        names.append(bridge_nickname)
    return names


def parse_message_event(
    data: Dict[str, Any],
    *,
    self_wxid: str = "",
    self_names: Optional[List[str]] = None,
    require_mention: bool = True,
) -> Optional[AdapterMessage]:
    """桥 message 事件 → AdapterMessage；丢弃（自己消息/空系统消息）返回 None。

    is_to_me 语义与 QQ 频道一致：私聊恒 True；群聊 require_mention 时按
    「@我」判定。trigger_mind 由 adapter 按策略层（dm/group policy）再裁决。
    """
    username = str(data.get("username") or "")
    if not username:
        return None

    if is_self_message(data, self_wxid):
        return None

    msg_type = str(data.get("type") or "文本")
    is_group = bool(data.get("is_group")) or username.endswith("@chatroom")
    content = str(data.get("content") or "")

    # 群聊文本正文带「wxid:\n」发送者前缀（DB 层形态），剥掉
    sender_username = str(data.get("sender_username") or "")
    if is_group and content.startswith(f"{sender_username}:\n"):
        content = content[len(sender_username) + 2:]

    body, quoted = extract_quote(content)

    segments: List[MessageSegment] = []
    text = body
    media = _MEDIA_TYPES.get(msg_type)
    if media is not None:
        seg_type, placeholder = media
        segments.append(MessageSegment(
            type=seg_type,
            content=placeholder,
            file_id=str(data.get("local_id") or ""),
        ))
        text = placeholder if not body.strip() else body
    elif msg_type == "文本":
        if body.strip():
            segments.append(MessageSegment(type=SegmentType.TEXT, content=body))
    else:
        # 动画表情/红包/音视频通话/系统消息等：保留类型占位，不进媒体段
        placeholder = f"[{msg_type}]"
        segments.append(MessageSegment(type=SegmentType.TEXT, content=placeholder))
        text = placeholder if not body.strip() else body

    if msg_type == "系统消息" and not body.strip():
        return None

    is_to_me = True
    if is_group and require_mention:
        is_to_me = detect_at_me(content, self_names or [])

    channel = AdapterChannel(
        channel_id=username,
        channel_type=ChannelType.GROUP if is_group else ChannelType.PRIVATE,
        channel_name=str(data.get("chat_name") or username),
    )
    sender = AdapterUser(
        platform=PLATFORM,
        user_id=("wcauto_system" if is_group else username) if msg_type == "系统消息" else sender_username or username,
        user_name="微信系统" if msg_type == "系统消息" else str(data.get("sender_name") or sender_username or username),
    )

    return AdapterMessage(
        message_id=f"{username}:{data.get('shard') or ''}:{data.get('local_id') or ''}",
        sender=sender,
        channel=channel,
        content=text,
        segments=segments,
        kind=MessageKind.SYSTEM if msg_type == "系统消息" else MessageKind.CHAT,
        is_to_me=is_to_me,
        timestamp=float(data.get("create_time") or time.time()),
        reply_content=quoted,
    )
