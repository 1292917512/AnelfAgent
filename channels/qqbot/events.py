"""将 QQ 事件映射到 Anelf 会话。

模型看到普通入站消息、引用摘要、媒体段与会话内的系统事件。
文本与事件按实际长度占用动态 token，附件复用现有多模态管线。
会话内容从标准入站通道进入，不修改稳定前缀。
"""

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageKind,
    MessageSegment,
    SegmentType,
    notification_channel,
    notification_sender,
)
from agent.messages import build_entity_scope

_BASES = {"c2c": "/v2/users", "group": "/v2/groups", "channel": "/channels", "dm": "/dms"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")
_MENTION = re.compile(r'<@!?([\w-]+)>|<qqbot-at-user id="([\w-]+)"\s*/>')
_MESSAGES = {"C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE",
             "AT_MESSAGE_CREATE", "MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE"}
_NOTICES = {
    "FRIEND_ADD": "用户添加了机器人", "FRIEND_DEL": "用户删除了机器人",
    "C2C_MSG_REJECT": "用户关闭主动消息", "C2C_MSG_RECEIVE": "用户开启主动消息",
    "GROUP_ADD_ROBOT": "机器人加入群聊", "GROUP_DEL_ROBOT": "机器人离开群聊",
    "GROUP_MSG_REJECT": "群关闭主动消息", "GROUP_MSG_RECEIVE": "群开启主动消息",
    "GROUP_MEMBER_ADD": "成员加入群聊", "GROUP_MEMBER_REMOVE": "成员离开群聊",
    "GROUP_JOIN_REQUEST": "收到入群申请",
}
_PASSIVE_EVENTS = {"FRIEND_ADD", "C2C_MSG_RECEIVE", "GROUP_ADD_ROBOT", "GROUP_MSG_RECEIVE"}
_LIFECYCLE = {"GUILD_CREATE": "频道加入", "GUILD_UPDATE": "频道更新", "GUILD_DELETE": "频道移除",
              "CHANNEL_CREATE": "子频道创建", "CHANNEL_UPDATE": "子频道更新", "CHANNEL_DELETE": "子频道移除"}
_INTERACTION_NOTICES = {13: "消息反馈", 14: "用户清空 QQ 会话", 15: "故事集状态变更", 16: "用户切换模型",
                        18: "用户授权变更", 19: "群授权变更", 20: "群授权状态变更"}


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("QQ 会话缺少有效的平台 ID")
    return value


def reference_id(data: dict[str, Any], key: str = "msg_idx") -> str:
    """从场景扩展中提取指定索引，保留 REFIDX 尾部的等号。"""
    scene = data.get("message_scene")
    if isinstance(scene, dict):
        for value in scene.get("ext") or []:
            if isinstance(value, str) and value.startswith(key + "="):
                return value.partition("=")[2]
    return ""


@dataclass(frozen=True)
class Target:
    """持久可解析的发送目标。频道私信的虚拟 guild ID 与用户 ID 分开保存。"""

    kind: str
    id: str
    peer: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _BASES:
            raise ValueError("未知的 QQ 目标类型")
        identifier(self.id)
        if self.kind == "dm":
            identifier(self.peer)
        elif self.peer:
            raise ValueError("仅频道私信目标带有 peer")

    @classmethod
    def parse(cls, address: str) -> "Target":
        parts = address.split(".")
        if len(parts) != (3 if parts[0] == "dm" else 2):
            raise ValueError("QQ 目标须为 c2c.ID、group.ID、channel.ID 或 dm.GUILD.USER")
        return cls(*parts)

    @classmethod
    def for_session(cls, address: str, session: str = "") -> "Target":
        if session and session != address:
            target = cls.parse(session)
            if target.kind != "dm" or target.peer != address:
                raise ValueError("QQ 会话与发送目标不匹配")
            return target
        return cls.parse(address)

    @property
    def address(self) -> str:
        return ".".join(filter(None, (self.kind, self.id, self.peer)))

    @property
    def base(self) -> str:
        return f"{_BASES[self.kind]}/{self.id}"

    @property
    def scope(self) -> str:
        kind = "group" if self.kind in {"group", "channel"} else "user"
        return build_entity_scope(kind, "qqbot", self.peer or self.address, self.address)

    @property
    def channel(self) -> AdapterChannel:
        group = self.kind in {"group", "channel"}
        return AdapterChannel(channel_id=self.address, channel_type=ChannelType.GROUP if group
                              else ChannelType.PRIVATE)


def _media_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    url = value.strip()
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    try:
        parsed = urlsplit(url)
        return url if parsed.scheme in ("http", "https") and parsed.hostname else ""
    except ValueError:
        return ""


def _attachments(data: dict[str, Any], guild: bool) -> list[MessageSegment]:
    segments: list[MessageSegment] = []
    for item in data.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("content_type") or ("image/*" if guild else "")).lower()
        wav = _media_url(item.get("voice_wav_url"))
        url = wav or _media_url(item.get("url"))
        if not url:
            continue
        kind = {"image": SegmentType.IMAGE, "audio": SegmentType.VOICE,
                "voice": SegmentType.VOICE, "video": SegmentType.VIDEO}.get(mime.split("/")[0], SegmentType.FILE)
        size = str(item.get("size") or 0)
        segments.append(MessageSegment(type=SegmentType.VOICE if wav else kind, url=url,
                                       mime_type="audio/wav" if wav else mime, file_name=item.get("filename") or "",
                                       file_size=int(size) if size.isascii() and size.isdigit() and len(size) < 20 else 0,
                                       content=str(item.get("asr_refer_text") or "")[:1000]))
    return segments


def _card(data: dict[str, Any]) -> str:
    ark = data.get("ark_data") or {}
    fields = ark.get("fields") or {}
    return " ".join(str(v)[:300] for v in (ark.get("ark_name") or ark.get("ark_type"),
                    fields.get("title") or ark.get("prompt"), fields.get("desc"), fields.get("jump_url")) if v)[:800]


def _history_preview(data: dict[str, Any]) -> str:
    """普通合并消息及无元素索引的记录分享只作为正文，不冒充可引用的原消息。"""
    elements = data.get("msg_elements") or []
    if data.get("message_type") not in (101, 102, 103):
        return ""
    if data.get("message_type") == 103 and any(e.get("msg_idx") for e in elements if isinstance(e, dict)):
        return ""
    snippets: list[str] = []
    for el in islice(elements, 12):
        if isinstance(el, dict):
            text = el.get("content") or _card(el)
            if text:
                snippets.append(str(text)[:400])
    return ("转发记录：" + "\n".join(snippets))[:1600] if snippets else ""


def _chat(event: str, data: dict[str, Any], self_id: str, require_mention: bool, self_name: str) -> AdapterMessage | None:
    author = data.get("author") or {}
    if author.get("bot") or (self_id and author.get("id") == self_id):
        return None
    if event == "C2C_MESSAGE_CREATE":
        target = Target("c2c", identifier(author.get("user_openid") or author.get("id")))
        sender = target.address
    elif event.startswith("GROUP_"):
        target = Target("group", identifier(data.get("group_openid")))
        sender = identifier(author.get("member_openid") or author.get("id"))
    elif event == "DIRECT_MESSAGE_CREATE":
        target = Target("dm", identifier(data.get("guild_id")), identifier(author.get("id")))
        sender = target.peer
    else:
        target = Target("channel", identifier(data.get("channel_id")))
        sender = identifier(author.get("id"))

    raw = str(data.get("content") or "")
    mentions = {str(m.get("id")): m for m in data.get("mentions") or [] if isinstance(m, dict)}
    to_me = (target.channel.channel_type == ChannelType.PRIVATE or "AT_MESSAGE" in event
             or self_id in mentions or any(m.get("is_you") and m.get("scope") != "all"
                                           for m in mentions.values()))

    def mention(match: re.Match[str]) -> str:
        nonlocal to_me
        uid = match[1] or match[2]
        info = mentions.get(uid, {})
        if uid == self_id or info.get("is_you"):
            to_me = True
            return ""
        return "@" + str(info.get("username") or uid)

    reference = data.get("message_reference") or {}
    ref_idx = reference_id(data, "ref_msg_idx") if data.get("message_type") == 103 else ""
    quoted = next((el for el in data.get("msg_elements") or [] if ref_idx and isinstance(el, dict)
                   and el.get("msg_idx") == ref_idx), {})
    quoted_author = quoted.get("author") or {}
    if quoted_author.get("bot") and (self_id == quoted_author.get("id") or
                                      self_name and self_name == quoted_author.get("username")):
        to_me = True
    first = _MENTION.match(raw.lstrip()) if quoted and target.kind == "group" else None
    if first:
        uid = first[1] or first[2]
        quoted_ids = {quoted_author.get(key) for key in ("id", "user_openid", "member_openid")} - {None, ""}
        if uid in quoted_ids or (not quoted_ids and uid in mentions):
            to_me = to_me or uid == self_id or bool(mentions.get(uid, {}).get("is_you"))
            raw = raw.lstrip()[first.end():]
    content = html.unescape(_MENTION.sub(mention, raw)).strip() or _card(data) or _history_preview(data)
    media = _attachments(data, guild=target.kind in {"channel", "dm"})
    transcripts = [seg.content for seg in media if seg.type == SegmentType.VOICE and seg.content]
    if transcripts:
        content = "\n".join(filter(None, (content, "语音转写：" + " ".join(transcripts))))
    if quoted and to_me:
        media.extend(s for s in _attachments(quoted, guild=False) if s.type == SegmentType.IMAGE)
    segments = ([MessageSegment(content=content)] if content else []) + media
    if not segments and not ("AT_MESSAGE" in event or (to_me and (raw.strip() or quoted))):
        return None
    message = AdapterMessage(
        message_id=data["id"], sender=AdapterUser(platform="qqbot", user_id=sender,
                                                  user_name=author.get("username") or sender),
        channel=target.channel, content=content or ("收到附件" if segments else "@机器人"),
        segments=segments, is_to_me=to_me, trigger_mind=to_me or not require_mention,
        reply_to_id=reference.get("message_id") or quoted.get("msg_idx") or "",
        reply_content=str(quoted.get("content") or _card(quoted))[:200],
    )
    if data.get("timestamp"):
        message.timestamp = datetime.fromisoformat(data["timestamp"]).timestamp()
    return message


def _interaction(payload: dict[str, Any], data: dict[str, Any]) -> AdapterMessage | None:
    interaction_type = data.get("type")
    if not isinstance(interaction_type, int):
        return None
    actionable = interaction_type in (11, 12)
    if not actionable and interaction_type not in _INTERACTION_NOTICES:
        return None
    resolved = (data.get("data") or {}).get("resolved") or {}
    chat_type = data.get("chat_type")
    if chat_type == 1 or (chat_type is None and data.get("group_openid")):
        target = Target("group", identifier(data.get("group_openid")))
        actor = identifier(data.get("group_member_openid") or (None if actionable else "qqbot-system"))
    elif chat_type == 2 or (chat_type is None and data.get("user_openid")):
        target = Target("c2c", identifier(data.get("user_openid")))
        actor = target.address
    elif chat_type == 0:
        target = Target("channel", identifier(data.get("channel_id")))
        actor = identifier(resolved.get("user_id"))
    else:
        return None
    if not data.get("id"):
        raise ValueError("QQ 互动缺少 ID")
    if actionable:
        label = "按钮" if interaction_type == 11 else "快捷菜单"
        content = (f"用户点击{label} {resolved.get('button_id') or resolved.get('feature_id') or ''} "
                   f"{str(resolved.get('button_data') or '')[:512]}")
        message_id = "event." + (payload.get("id") or data["id"])
    else:
        content = _INTERACTION_NOTICES[interaction_type]
        if interaction_type == 13 and resolved.get("feedback_opt") in ("LIKE", "UNLIKE"):
            content += f" {resolved['feedback_opt']}（选中={resolved.get('checked')}）"
        message_id = payload.get("id") or data["id"]
    click = AdapterMessage(message_id=message_id, channel=target.channel, content=content,
                           sender=AdapterUser(platform="qqbot", user_id=actor), kind=MessageKind.EVENT,
                           is_to_me=actionable, trigger_mind=actionable, reply_to_id=resolved.get("message_id") or "")
    if data.get("timestamp"):
        click.timestamp = datetime.fromisoformat(data["timestamp"]).timestamp()
    return click


def parse_event(payload: dict[str, Any], self_id: str, require_mention: bool, self_name: str = "") -> AdapterMessage | None:
    event, data = payload.get("t"), payload.get("d")
    if not isinstance(event, str) or not isinstance(data, dict):
        raise ValueError("QQ Dispatch 缺少事件类型或数据")
    if event in _MESSAGES:
        return _chat(event, data, self_id, require_mention, self_name)
    if event in _LIFECYCLE:
        return AdapterMessage(message_id=_event_id(payload), channel=notification_channel("QQ"),
                              sender=notification_sender("qqbot", "QQ"), kind=MessageKind.NOTIFICATION,
                              content=f"{_LIFECYCLE[event]}：{data.get('name') or ''}（{data['id']}）",
                              trigger_mind=False)
    if event == "INTERACTION_CREATE":
        return _interaction(payload, data)
    content = _NOTICES.get(event, "")
    if event in {"MESSAGE_DELETE", "PUBLIC_MESSAGE_DELETE", "DIRECT_MESSAGE_DELETE"}:
        deleted = data["message"]
        actor = identifier(data["op_user"]["id"])
        if event == "DIRECT_MESSAGE_DELETE":
            peer = identifier(deleted["author"]["id"])
            if peer == self_id:
                return None  # 机器人自己的私信撤回不具备对端身份。
            target = Target("dm", identifier(deleted["guild_id"]), peer)
            actor = peer
        else:
            target = Target("channel", identifier(deleted["channel_id"]))
        content = f"消息已撤回（消息 ID {deleted['id']}，操作者 {data['op_user']['id']}）"
    elif event in {"MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"}:
        target = Target("channel", identifier(data["channel_id"]))
        actor = identifier(data["user_id"])
        action = "添加" if event == "MESSAGE_REACTION_ADD" else "移除"
        content = f"用户{action}了表情回应 {data['emoji']['id']}（目标 {data['target']['id']}）"
    elif event in {"MESSAGE_AUDIT_PASS", "MESSAGE_AUDIT_REJECT"}:
        if data.get("user_openid"):
            target = Target("c2c", identifier(data["user_openid"]))
        elif data.get("group_openid"):
            target = Target("group", identifier(data["group_openid"]))
        elif data.get("channel_id"):
            target = Target("channel", identifier(data["channel_id"]))
        else:
            return None
        actor = target.address if target.kind == "c2c" else "qqbot-system"
        status = "通过" if event == "MESSAGE_AUDIT_PASS" else "被拒绝"
        content = f"机器人消息审核{status}（审核 ID {data['audit_id']}）"
    elif event == "SUBSCRIBE_MESSAGE_STATUS":
        if data.get("group_openid"):
            target = Target("group", identifier(data["group_openid"]))
            actor = data.get("openid") or "qqbot-system"
        else:
            target = Target("c2c", identifier(data.get("openid")))
            actor = target.address
        templates = [f"{row['template_id']} (op={row['op']})" for row in (data.get("result") or [])[:20]]
        content = "订阅消息授权变更：" + "、".join(templates)
    elif not content:
        return None
    elif event.startswith("GROUP_"):
        target = Target("group", identifier(data.get("group_openid")))
        actor = identifier(data.get("member_openid") or data.get("op_member_openid"))
    else:
        target = Target("c2c", identifier(data.get("openid")))
        actor = target.address
    event_id = _event_id(payload)
    if event in _PASSIVE_EVENTS and payload.get("id"):
        event_id = "event." + event_id
    if event == "GROUP_JOIN_REQUEST":
        content += f"（申请 ID {data['join_request_id']}）"
    message = AdapterMessage(message_id=event_id, sender=AdapterUser(platform="qqbot", user_id=actor),
                             channel=target.channel, content=content, kind=MessageKind.EVENT, trigger_mind=False)
    if isinstance(data.get("timestamp"), (float, int)):
        message.timestamp = float(data["timestamp"])
    return message


def _event_id(payload: dict[str, Any]) -> str:
    return payload.get("id") or hashlib.sha256(json.dumps(
        [payload["t"], payload["d"]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
