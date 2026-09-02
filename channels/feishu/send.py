"""飞书消息发送封装 -- 文本/富文本/媒体/回复/编辑/删除/转发/置顶/表情回应/历史读取。

所有函数返回字典（频道方法层负责 JSON 化与错误归因）。
lark-oapi API 调用为同步，统一用 asyncio.to_thread() 包装；
响应失败经 errors.raise_for_fail 抛 FeishuApiError，由上层 to_error_json 归因。

Model Experience:
1. 模型看到什么：reaction / get_chat_history / get_message 返回结构化 JSON，
   错误带 cause + hint + retryable（见 errors.py 错误码表）。
2. token 影响：get_chat_history 默认 20 条、单条文本截断 500 字符，防长群聊灌爆上下文。
3. 缓存影响：零（工具结果走 tool_chain 尾部动态区，不触碰前缀层）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageRequest,
    ForwardMessageRequest,
    ForwardMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from .errors import raise_for_fail
from .helpers import chunk_text, looks_like_markdown, parse_message_content
from .media import upload_file, upload_image

# ------------------------------------------------------------------
# 发送文本消息
# ------------------------------------------------------------------


def _should_attach_reply(reply_to_mode: str, chunk_index: int) -> bool:
    """按引用策略判断当前分块是否挂引用。"""
    if reply_to_mode == "off":
        return False
    if reply_to_mode == "all":
        return True
    return chunk_index == 0  # first


async def send_text(
    client: lark.Client,
    chat_id: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
    text_limit: int = 4000,
    reply_to_mode: str = "first",
    render_markdown: bool = True,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """发送文本消息（自动分块；含 Markdown 语法时走 post 富文本渲染）。"""
    use_post = render_markdown and looks_like_markdown(text)
    chunks = chunk_text(text, text_limit)
    first_msg_id = ""
    message_ids: List[str] = []
    for i, chunk in enumerate(chunks):
        attach_reply = bool(reply_to) and _should_attach_reply(reply_to_mode, i)
        if use_post:
            result = await send_post(
                client, chat_id, chunk,
                reply_to=reply_to if attach_reply else None,
                reply_in_thread=reply_in_thread,
            )
        elif attach_reply and reply_to:
            result = await _reply_message(
                client, reply_to, chunk, reply_in_thread=reply_in_thread,
            )
        else:
            result = await _create_message(client, chat_id, "text", json.dumps({"text": chunk}))
        mid = result.get("message_id", "")
        if mid:
            message_ids.append(mid)
        if i == 0:
            first_msg_id = mid
    return {
        "message_id": first_msg_id,
        "message_ids": message_ids,
        "chat_id": chat_id,
        "chunks": len(chunks),
        "rendered_as": "post" if use_post else "text",
    }


# ------------------------------------------------------------------
# 发送富文本 (post) 消息
# ------------------------------------------------------------------


def _build_post_content(text: str) -> str:
    """构造 post/md 富文本内容 JSON（飞书 md 标签支持 Markdown 渲染）。"""
    return json.dumps({
        "zh_cn": {
            "content": [[{"tag": "md", "text": text}]],
        },
    })


async def send_post(
    client: lark.Client,
    chat_id: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """以 post 格式发送消息（支持 Markdown 渲染）。"""
    content = _build_post_content(text)
    if reply_to:
        return await _reply_message(
            client, reply_to, text, msg_type="post",
            content_override=content, reply_in_thread=reply_in_thread,
        )
    return await _create_message(client, chat_id, "post", content)


# ------------------------------------------------------------------
# 发送图片
# ------------------------------------------------------------------


async def send_photo(
    client: lark.Client,
    chat_id: str,
    photo_path: str,
    *,
    caption: str = "",
    reply_to: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """上传并发送图片。"""
    image_key = await upload_image(client, photo_path)
    content = json.dumps({"image_key": image_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(
            client, reply_to, "", msg_type="image",
            content_override=content, reply_in_thread=reply_in_thread,
        )
    return await _create_message(client, chat_id, "image", content)


# ------------------------------------------------------------------
# 发送文件/音频/视频
# ------------------------------------------------------------------


async def send_file(
    client: lark.Client,
    chat_id: str,
    file_path: str,
    *,
    file_type: str = "stream",
    caption: str = "",
    reply_to: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """上传并发送文件。"""
    file_key = await upload_file(client, file_path, file_type=file_type)
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(
            client, reply_to, "", msg_type="file",
            content_override=content, reply_in_thread=reply_in_thread,
        )
    return await _create_message(client, chat_id, "file", content)


async def send_audio(
    client: lark.Client,
    chat_id: str,
    audio_path: str,
    *,
    caption: str = "",
    reply_to: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """上传并发送音频。"""
    file_key = await upload_file(client, audio_path, file_type="opus")
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(
            client, reply_to, "", msg_type="audio",
            content_override=content, reply_in_thread=reply_in_thread,
        )
    return await _create_message(client, chat_id, "audio", content)


async def send_video(
    client: lark.Client,
    chat_id: str,
    video_path: str,
    *,
    caption: str = "",
    reply_to: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """上传并发送视频。"""
    file_key = await upload_file(client, video_path, file_type="mp4")
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(
            client, reply_to, "", msg_type="media",
            content_override=content, reply_in_thread=reply_in_thread,
        )
    return await _create_message(client, chat_id, "media", content)


# ------------------------------------------------------------------
# 消息操作
# ------------------------------------------------------------------


async def edit_message(
    client: lark.Client,
    message_id: str,
    text: str,
    *,
    format: str = "text",
) -> Dict[str, Any]:
    """编辑已发送的消息（飞书限 24 小时内；format=markdown 时以 post/md 渲染）。

    注意：飞书要求编辑后的 msg_type 与原消息一致，text 格式只能编辑文本消息，
    markdown 格式只能编辑 post 消息，类型不匹配会返回平台错误。
    """

    def _do() -> Dict[str, Any]:
        content = _build_post_content(text) if format == "markdown" else json.dumps({"text": text})
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(
                PatchMessageRequestBody.builder()
                .content(content)
                .build()
            ).build()
        resp = client.im.v1.message.patch(req)
        raise_for_fail(resp, "编辑消息")
        return {"edited": True, "message_id": message_id, "rendered_as": "post" if format == "markdown" else "text"}

    return await asyncio.to_thread(_do)


async def delete_message(
    client: lark.Client,
    message_id: str,
) -> Dict[str, Any]:
    """撤回/删除消息。"""

    def _do() -> Dict[str, Any]:
        req = DeleteMessageRequest.builder() \
            .message_id(message_id) \
            .build()
        resp = client.im.v1.message.delete(req)
        raise_for_fail(resp, "删除消息")
        return {"deleted": True, "message_id": message_id}

    return await asyncio.to_thread(_do)


async def forward_message(
    client: lark.Client,
    message_id: str,
    target_chat_id: str,
) -> Dict[str, Any]:
    """转发消息到另一个会话。"""

    def _do() -> Dict[str, Any]:
        req = ForwardMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(
                ForwardMessageRequestBody.builder()
                .receive_id(target_chat_id)
                .build()
            ).build()
        resp = client.im.v1.message.forward(req)
        raise_for_fail(resp, "转发消息")
        new_id = resp.data.message_id if resp.data else ""
        return {"forwarded": True, "message_id": new_id}

    return await asyncio.to_thread(_do)


async def pin_message(client: lark.Client, message_id: str) -> Dict[str, Any]:
    """置顶消息。"""
    from lark_oapi.api.im.v1 import CreatePinRequest, CreatePinRequestBody

    def _do() -> Dict[str, Any]:
        body = CreatePinRequestBody.builder().message_id(message_id).build()
        req = CreatePinRequest.builder().request_body(body).build()
        resp = client.im.v1.pin.create(req)
        raise_for_fail(resp, "置顶消息")
        return {"pinned": True, "message_id": message_id}

    return await asyncio.to_thread(_do)


async def unpin_message(client: lark.Client, message_id: str) -> Dict[str, Any]:
    """取消置顶消息。"""
    from lark_oapi.api.im.v1 import DeletePinRequest

    def _do() -> Dict[str, Any]:
        req = DeletePinRequest.builder().message_id(message_id).build()
        resp = client.im.v1.pin.delete(req)
        raise_for_fail(resp, "取消置顶")
        return {"unpinned": True, "message_id": message_id}

    return await asyncio.to_thread(_do)


# ------------------------------------------------------------------
# 表情回应（reaction）
# ------------------------------------------------------------------

# 常用 emoji 别名 → 飞书 emoji_type（未知值大写后透传，由平台校验报错）
_EMOJI_ALIASES: Dict[str, str] = {
    "👍": "THUMBSUP", "thumbsup": "THUMBSUP", "赞": "THUMBSUP",
    "👎": "THUMBSDOWN", "thumbsdown": "THUMBSDOWN",
    "👌": "OK", "ok": "OK",
    "✅": "DONE", "done": "DONE", "完成": "DONE",
    "❤️": "HEART", "❤": "HEART", "heart": "HEART",
    "😄": "SMILE", "😊": "SMILE", "smile": "SMILE",
    "😂": "JOY", "joy": "JOY",
    "👏": "APPLAUSE", "applause": "APPLAUSE",
    "🙏": "THANKS", "thanks": "THANKS", "谢谢": "THANKS",
    "💪": "MUSCLE", "muscle": "MUSCLE",
    "👊": "FIST", "fist": "FIST",
    "😢": "CRY", "cry": "CRY",
}


def resolve_emoji_type(emoji: str) -> str:
    """将 emoji 字符/别名解析为飞书 emoji_type。"""
    value = emoji.strip()
    if not value:
        raise ValueError("emoji 不能为空；常用值：👍(THUMBSUP) 👌(OK) ✅(DONE) ❤️(HEART) 😄(SMILE)")
    mapped = _EMOJI_ALIASES.get(value) or _EMOJI_ALIASES.get(value.lower())
    if mapped:
        return mapped
    if value.upper() == value and value.replace("_", "").isalpha():
        return value  # 已是大写枚举形式，透传由平台校验
    raise ValueError(
        f"无法识别的表情 '{emoji}'；支持 emoji 字符（如 👍✅❤️）或飞书 emoji_type 枚举"
        "（如 THUMBSUP / OK / DONE / HEART / SMILE）"
    )


async def add_reaction(
    client: lark.Client,
    message_id: str,
    emoji: str,
) -> Dict[str, Any]:
    """对消息添加表情回应。返回 {reaction_id, emoji_type}（reaction_id 供移除使用）。"""
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
    )

    emoji_type = resolve_emoji_type(emoji)

    def _do() -> Dict[str, Any]:
        body = CreateMessageReactionRequestBody.builder() \
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build()) \
            .build()
        req = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        resp = client.im.v1.message_reaction.create(req)
        raise_for_fail(resp, "添加表情回应")
        reaction_id = getattr(resp.data, "reaction_id", "") if resp.data else ""
        return {"reacted": True, "message_id": message_id,
                "emoji_type": emoji_type, "reaction_id": reaction_id or ""}

    return await asyncio.to_thread(_do)


async def remove_reaction(
    client: lark.Client,
    message_id: str,
    reaction_id: str,
) -> Dict[str, Any]:
    """移除 Bot 此前添加的表情回应（reaction_id 来自添加时的返回值）。"""
    from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

    def _do() -> Dict[str, Any]:
        req = DeleteMessageReactionRequest.builder() \
            .message_id(message_id) \
            .reaction_id(reaction_id) \
            .build()
        resp = client.im.v1.message_reaction.delete(req)
        raise_for_fail(resp, "移除表情回应")
        return {"removed": True, "message_id": message_id, "reaction_id": reaction_id}

    return await asyncio.to_thread(_do)


# ------------------------------------------------------------------
# 查询
# ------------------------------------------------------------------


async def get_chat_info(client: lark.Client, chat_id: str) -> Dict[str, Any]:
    """查询群聊详细信息。"""
    from lark_oapi.api.im.v1 import GetChatRequest

    def _do() -> Dict[str, Any]:
        req = GetChatRequest.builder().chat_id(chat_id).build()
        resp = client.im.v1.chat.get(req)
        raise_for_fail(resp, "查询群信息")
        data = resp.data
        if not data:
            return {"chat_id": chat_id}
        return {
            "chat_id": chat_id,
            "name": getattr(data, "name", ""),
            "description": getattr(data, "description", ""),
            "owner_id": getattr(data, "owner_id", ""),
            "chat_mode": getattr(data, "chat_mode", ""),
            "chat_type": getattr(data, "chat_type", ""),
            "member_count": getattr(data, "user_count", 0),
        }

    return await asyncio.to_thread(_do)


_MEMBER_PAGE_SIZE = 100
_MEMBER_MAX_PAGES = 5  # 单群最多返回 500 名成员，防超大群结果膨胀


async def get_chat_members(client: lark.Client, chat_id: str) -> Dict[str, Any]:
    """查询群聊成员列表（自动分页，上限 500 人）。"""
    from lark_oapi.api.im.v1 import GetChatMembersRequest

    def _do() -> Dict[str, Any]:
        members: List[Dict[str, Any]] = []
        page_token = ""
        has_more = False
        for _ in range(_MEMBER_MAX_PAGES):
            builder = GetChatMembersRequest.builder() \
                .chat_id(chat_id) \
                .member_id_type("open_id") \
                .page_size(_MEMBER_PAGE_SIZE)
            if page_token:
                builder = builder.page_token(page_token)
            resp = client.im.v1.chat_members.get(builder.build())
            raise_for_fail(resp, "查询成员")
            data = resp.data
            if not data:
                break
            for m in (data.items or []):
                members.append({
                    "member_id": getattr(m, "member_id", ""),
                    "name": getattr(m, "name", ""),
                    "member_id_type": getattr(m, "member_id_type", ""),
                })
            has_more = bool(getattr(data, "has_more", False))
            page_token = getattr(data, "page_token", "") or ""
            if not has_more or not page_token:
                break
        result: Dict[str, Any] = {"members": members, "count": len(members)}
        if has_more:
            result["truncated"] = True
            result["hint"] = f"成员超过 {len(members)} 人，仅返回前 {len(members)} 人"
        return result

    return await asyncio.to_thread(_do)


_HISTORY_MAX_LIMIT = 50
_HISTORY_TEXT_LIMIT = 500


def _serialize_history_message(item: Any) -> Dict[str, Any]:
    """将 SDK Message 对象序列化为 AI 可读的历史消息条目。"""
    msg_type = getattr(item, "msg_type", "") or ""
    body = getattr(item, "body", None)
    raw_content = getattr(body, "content", "") if body else ""
    text = parse_message_content(raw_content, msg_type)
    if len(text) > _HISTORY_TEXT_LIMIT:
        text = text[:_HISTORY_TEXT_LIMIT] + "…"
    sender = getattr(item, "sender", None)
    sender_id = getattr(sender, "id", None) if sender else None
    create_time = str(getattr(item, "create_time", "") or "")
    time_str = ""
    if create_time.isdigit():
        time_str = time.strftime("%m-%d %H:%M", time.localtime(int(create_time) / 1000))
    return {
        "message_id": getattr(item, "message_id", "") or "",
        "sender_open_id": getattr(sender_id, "open_id", "") or "",
        "msg_type": msg_type,
        "time": time_str,
        "text": text,
    }


async def get_chat_history(
    client: lark.Client,
    chat_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """读取会话最近消息（新消息在前，默认 20 条、上限 50 条）。"""
    from lark_oapi.api.im.v1 import ListMessageRequest

    page_size = max(1, min(int(limit), _HISTORY_MAX_LIMIT))

    def _do() -> Dict[str, Any]:
        req = ListMessageRequest.builder() \
            .container_id_type("chat") \
            .container_id(chat_id) \
            .sort_type("ByCreateTimeDesc") \
            .page_size(page_size) \
            .build()
        resp = client.im.v1.message.list(req)
        raise_for_fail(resp, "读取会话历史")
        items = (resp.data.items if resp.data else None) or []
        messages = [_serialize_history_message(m) for m in items]
        return {"messages": messages, "count": len(messages), "chat_id": chat_id}

    return await asyncio.to_thread(_do)


async def get_message(client: lark.Client, message_id: str) -> Dict[str, Any]:
    """读取单条消息内容（可查看被引用消息的原文）。

    注意：合并转发消息（merge_forward）飞书未开放入站展开接口，
    该类型消息只能读到占位文本。
    """
    from lark_oapi.api.im.v1 import GetMessageRequest

    def _do() -> Dict[str, Any]:
        req = GetMessageRequest.builder().message_id(message_id).build()
        resp = client.im.v1.message.get(req)
        raise_for_fail(resp, "读取消息")
        items = (resp.data.items if resp.data else None) or []
        if not items:
            raise RuntimeError(f"飞书读取消息失败: 消息不存在或无权限 ({message_id})")
        return _serialize_history_message(items[0])

    return await asyncio.to_thread(_do)


async def get_bot_info(client: lark.Client) -> Dict[str, str]:
    """获取 Bot 自身信息（open_id, app_name）。

    通过 lark-oapi 内部的 token 管理获取 tenant_access_token，
    然后直接调用 /open-apis/bot/v3/info/ 接口。
    """
    import httpx

    def _do() -> Dict[str, str]:
        config = client._config
        domain = config.domain or "https://open.feishu.cn"
        # 先获取 tenant_access_token
        token_resp = httpx.post(
            f"{domain}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": config.app_id, "app_secret": config.app_secret},
            timeout=15,
        )
        token_data = token_resp.json()
        token = token_data.get("tenant_access_token", "")
        if not token:
            raise RuntimeError(f"获取 tenant_access_token 失败: {token_data.get('msg', 'unknown')}")

        # 获取 Bot 信息
        resp = httpx.get(
            f"{domain}/open-apis/bot/v3/info/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = resp.json()
        if data.get("code", -1) != 0:
            raise RuntimeError(f"获取 Bot 信息失败: {data.get('msg', 'unknown')}")
        bot = data.get("bot", {})
        return {
            "open_id": bot.get("open_id", ""),
            "app_name": bot.get("app_name", ""),
        }

    return await asyncio.to_thread(_do)


# ------------------------------------------------------------------
# 底层辅助
# ------------------------------------------------------------------


async def _create_message(
    client: lark.Client,
    chat_id: str,
    msg_type: str,
    content: str,
) -> Dict[str, Any]:
    """创建消息（底层封装）。"""

    def _do() -> Dict[str, Any]:
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            ).build()
        resp = client.im.v1.message.create(req)
        raise_for_fail(resp, "发送消息")
        msg_id = resp.data.message_id if resp.data else ""
        return {"message_id": msg_id, "chat_id": chat_id}

    return await asyncio.to_thread(_do)


async def _reply_message(
    client: lark.Client,
    reply_to_id: str,
    text: str,
    *,
    msg_type: str = "text",
    content_override: Optional[str] = None,
    reply_in_thread: bool = False,
) -> Dict[str, Any]:
    """回复消息（底层封装）。"""
    content = content_override or json.dumps({"text": text})

    def _do() -> Dict[str, Any]:
        body_builder = ReplyMessageRequestBody.builder() \
            .msg_type(msg_type) \
            .content(content)
        if reply_in_thread:
            body_builder = body_builder.reply_in_thread(True)
        req = ReplyMessageRequest.builder() \
            .message_id(reply_to_id) \
            .request_body(body_builder.build()) \
            .build()
        resp = client.im.v1.message.reply(req)
        raise_for_fail(resp, "回复消息")
        msg_id = resp.data.message_id if resp.data else ""
        return {"message_id": msg_id}

    return await asyncio.to_thread(_do)
