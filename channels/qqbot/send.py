"""QQ 会话出站，按目标选择协议、分配被动回复序号并保留逐段送达凭据。"""

import asyncio
import html
import json
import time
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from agent.channel.schemas import SegmentType, SendRequest, SendResponse, SendSegment
from channels._shared.text import split_text
from core.tags import replace_tags, tag_label
from core.tool_context import request_trace

from .client import QQAPI, DeliveryUnconfirmed, QQAPIError
from .events import Target, identifier
from .media import guild_image, upload_media

_MEDIA = {SegmentType.IMAGE: 1, SegmentType.VIDEO: 2, SegmentType.AUDIO: 3,
          SegmentType.VOICE: 3, SegmentType.FILE: 4}


def _text(content: str, target: Target) -> str:
    def replace(key: str, value: str) -> str:
        if key != "at_uid":
            return tag_label(key, value)
        if target.kind not in {"group", "channel"}:
            return "@" + value
        return "@everyone" if value == "all" else f"<@{identifier(value)}>"

    return replace_tags(html.escape(content, quote=False), replace)


@dataclass
class ReplyState:
    sequence: int = 0
    reference: str = ""
    received_at: float = 0
    sent: bool = False


@dataclass(frozen=True)
class Reply:
    anchor: str
    reference: str = ""


class QQReceipt(SendResponse):
    outcome: Literal["unknown"] | None = None
    retryable: bool | None = None


class Replies:
    """同一 AppID 共用的有界回复记录，重连只替换传输。"""

    def __init__(self) -> None:
        self._latest: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._replies: OrderedDict[tuple[str, str], ReplyState] = OrderedDict()

    def _state(self, address: str, message_id: str) -> ReplyState:
        key = (address, message_id)
        state = self._replies.setdefault(key, ReplyState())
        self._replies.move_to_end(key)
        if len(self._replies) > 4096:
            self._replies.popitem(last=False)
        return state

    def remember(self, address: str, message_id: str, timestamp: float, reference: str = "") -> None:
        state = self._state(address, message_id)
        state.reference, state.received_at = reference, timestamp
        current = self._latest.get(address)
        if current is None or timestamp >= current[1]:
            self._latest[address] = (message_id, timestamp)
            self._latest.move_to_end(address)
            if len(self._latest) > 2048:
                self._latest.popitem(last=False)

    def anchor(self, target: Target, reply_to: str | None) -> str:
        if reply_to:
            return reply_to
        window = 3600 if target.kind == "c2c" else 300
        trace = request_trace()
        if trace:
            message_id = trace["message_id"]
            if message_id and trace["scope"] == target.scope:
                state = self._replies.get((target.address, message_id))
                if state and state.received_at and time.time() - state.received_at >= window:
                    raise ValueError("触发本轮的 QQ 消息已超过被动回复窗口")
                # 恢复后仍以宿主绑定的原消息请求平台校验，绝不改挂其他人的消息。
                return message_id
            return ""
        recent = self._latest.get(target.address)
        if recent and 0 <= time.time() - recent[1] < window:
            return recent[0]
        return ""

    def fields(self, target: Target, reply: Reply) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if reply.reference:
            fields["message_reference"] = {"message_id": reply.reference}
        if reply.anchor.startswith("event."):
            fields["event_id"] = reply.anchor.removeprefix("event.")
        elif reply.anchor:
            fields["msg_id"] = reply.anchor
            if target.kind in {"group", "c2c"}:
                state = self._state(target.address, reply.anchor)
                state.sequence += 1
                fields["msg_seq"] = state.sequence
        return fields

    def sent(self, address: str, result: dict[str, Any]) -> None:
        state = self._state(address, result["id"])
        state.sent = True
        state.reference = (result.get("ext_info") or {}).get("ref_idx") or ""

    def resolve(self, target: Target, explicit: str | None) -> Reply:
        """引用已发消息只填可视引用；被动回复仍绑定当前入站消息。"""
        if explicit and explicit.startswith("REFIDX_") and target.kind in {"group", "c2c"}:
            return Reply(self.anchor(target, None), explicit)
        state = self._replies.get((target.address, explicit)) if explicit else None
        if state and state.sent:
            reference = state.reference if target.kind in ("group", "c2c") else explicit
            if not reference:
                raise ValueError("QQ 未提供该已发消息的引用索引")
            return Reply(self.anchor(target, None), reference)
        reference = ""
        if explicit and not explicit.startswith("event."):
            if target.kind in {"group", "c2c"}:
                reference = state.reference if state else ""
            else:
                reference = explicit
        return Reply(self.anchor(target, explicit), reference)


class Sender:
    def __init__(self, api: QQAPI, replies: Replies) -> None:
        self.api = api
        self.replies = replies

    async def send(self, request: SendRequest, max_length: int, *, gateway_online: bool) -> SendResponse:
        target = Target.for_session(request.channel.channel_id, str(request.extra.get("session_id") or ""))
        if target.kind == "channel" and not gateway_online:
            raise RuntimeError("QQ 子频道消息要求 WebSocket 网关在线，请检查接入方式和网关状态")
        reply = self.replies.resolve(target, request.reply_to)
        ids: list[str] = []
        failures: list[str] = []
        if not request.segments:
            raise ValueError("不能发送空消息")
        for segment in request.segments:
            try:
                if segment.type in (SegmentType.TEXT, SegmentType.AT):
                    for body in self._text_bodies(target, request, segment, max_length):
                        ids.append(await self._post(target, reply, body))
                else:
                    ids.append(await self._media(target, reply, segment))
            except DeliveryUnconfirmed as exc:
                failures.append(f"{segment.type.value}: {exc}，请核对平台回执后再发送")
                return QQReceipt(success=False, outcome="unknown", retryable=False, error="；".join(failures),
                                 message_ids=ids, message_id=ids[0] if ids else None)
            except Exception as exc:
                failures.append(f"{segment.type.value}: {exc}")
        return SendResponse(success=not failures, message_id=ids[0] if ids else None,
                            message_ids=ids, error="；".join(failures) or None)

    @staticmethod
    def _text_bodies(target: Target, request: SendRequest, segment: SendSegment,
                     max_length: int) -> Iterator[dict[str, Any]]:
        content = (segment.content if segment.type == SegmentType.TEXT else
                   tag_label("at_uid", segment.at_user_id))
        if not content.strip():
            raise ValueError("文本为空")
        if "card" in request.extra:
            card = request.extra["card"]
            if target.kind not in ("c2c", "group") or not 1 <= len(card["title"]) <= 50 or len(content) > 200:
                raise ValueError("图文卡片限 QQ 单聊或群聊，标题 1–50 字，描述 1–200 字")
            for url in (card["url"], card["pic_url"]):
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or len(url) > 1024:
                    raise ValueError("图文卡片链接必须是 1024 字符内的 HTTPS URL")
            body: dict[str, Any] = {"msg_type": 8, "card": {"type": "tuwen", "content": {
                "title": card["title"], "description": content, "url": card["url"], "pic_url": card["pic_url"],
            }}}
            yield body
            return
        # 分段数由全文决定，避免共享工具的默认截断。
        chunks = split_text(content, max_length, max_chunks=len(content))
        markdown = (request.parse_mode or segment.parse_mode or "").lower() == "markdown"
        for index, chunk in enumerate(chunks):
            body = ({"markdown": {"content": chunk}} if markdown else {"content": _text(chunk, target)})
            if target.kind in {"group", "c2c"}:
                body["msg_type"] = 2 if markdown else 0
            keyboard_id = request.extra.get("keyboard_id")
            if keyboard_id and index == len(chunks) - 1:
                body["keyboard"] = {"id": str(keyboard_id)}
            yield body

    async def _post(self, target: Target, reply: Reply, body: dict[str, Any],
                    *, image: tuple[str, bytes] | None = None) -> str:
        body.update(self.replies.fields(target, reply))
        path = target.base + "/messages"
        try:
            if image:
                form = {k: v for k, v in body.items() if not isinstance(v, dict)}
                files: dict[str, tuple[str, bytes] | tuple[str, bytes, str]] = {"file_image": image}
                files.update({k: (k, json.dumps({k: v}).encode(), "application/json")
                              for k, v in body.items() if isinstance(v, dict)})
                result = await self.api.request("POST", path, data=form, files=files)
            else:
                result = await self.api.request("POST", path, json=body)
        except (httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.WriteTimeout,
                httpx.RemoteProtocolError) as exc:
            raise DeliveryUnconfirmed("QQ 消息写入后未取得完整回执") from exc
        except QQAPIError as exc:
            if not exc.code and (exc.status >= 500 or 200 <= exc.status < 300):
                raise DeliveryUnconfirmed(str(exc)) from exc
            raise
        if not result.get("id"):
            raise DeliveryUnconfirmed("QQ 未返回已发送消息 ID，投递未确认")
        self.replies.sent(target.address, result)
        return str(result["id"])

    async def _media(self, target: Target, reply: Reply, segment: SendSegment) -> str:
        file_type = _MEDIA.get(segment.type)
        if not file_type or not segment.file_path:
            raise ValueError("不支持该消息段或缺少媒体路径")
        body: dict[str, Any] = {}
        if segment.caption:
            body["content"] = _text(segment.caption, target)
        if target.kind in {"group", "c2c"}:
            info = await upload_media(self.api, target, segment.file_path, file_type)
            body.update(msg_type=7, media={"file_info": info})
            return await self._post(target, reply, body)
        if file_type != 1:
            raise ValueError("频道子频道及频道私信仅支持图片附件")
        if segment.file_path.startswith(("https://", "http://")):
            body["image"] = segment.file_path
            return await self._post(target, reply, body)
        return await self._post(target, reply, body, image=await asyncio.to_thread(guild_image, segment.file_path))
