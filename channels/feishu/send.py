"""飞书消息发送封装 -- 文本/图片/文件/回复/编辑/删除/转发/置顶。

所有函数返回 JSON 字符串以符合 BaseChannel 约定。
lark-oapi API 调用为同步，统一用 asyncio.to_thread() 包装。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

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

from core.log import log

from .helpers import chunk_text
from .media import upload_file, upload_image


# ------------------------------------------------------------------
# 发送文本消息
# ------------------------------------------------------------------


async def send_text(
    client: lark.Client,
    chat_id: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
    text_limit: int = 4000,
) -> Dict[str, Any]:
    """发送文本消息（自动分块）。返回 {message_id, chat_id}。"""
    chunks = chunk_text(text, text_limit)
    first_msg_id = ""
    for i, chunk in enumerate(chunks):
        if reply_to and i == 0:
            result = await _reply_message(client, reply_to, chunk)
        else:
            result = await _create_message(client, chat_id, "text", json.dumps({"text": chunk}))
        if i == 0:
            first_msg_id = result.get("message_id", "")
    return {"message_id": first_msg_id, "chat_id": chat_id}


# ------------------------------------------------------------------
# 发送富文本 (post) 消息
# ------------------------------------------------------------------


async def send_post(
    client: lark.Client,
    chat_id: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """以 post 格式发送消息（支持 Markdown 渲染）。"""
    content = json.dumps({
        "zh_cn": {
            "content": [[{"tag": "md", "text": text}]],
        },
    })
    if reply_to:
        return await _reply_message(client, reply_to, text, msg_type="post", content_override=content)
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
) -> Dict[str, Any]:
    """上传并发送图片。"""
    image_key = await upload_image(client, photo_path)
    content = json.dumps({"image_key": image_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(client, reply_to, "", msg_type="image", content_override=content)
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
) -> Dict[str, Any]:
    """上传并发送文件。"""
    file_key = await upload_file(client, file_path, file_type=file_type)
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(client, reply_to, "", msg_type="file", content_override=content)
    return await _create_message(client, chat_id, "file", content)


async def send_audio(
    client: lark.Client,
    chat_id: str,
    audio_path: str,
    *,
    caption: str = "",
    reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """上传并发送音频。"""
    file_key = await upload_file(client, audio_path, file_type="opus")
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(client, reply_to, "", msg_type="audio", content_override=content)
    return await _create_message(client, chat_id, "audio", content)


async def send_video(
    client: lark.Client,
    chat_id: str,
    video_path: str,
    *,
    caption: str = "",
    reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """上传并发送视频。"""
    file_key = await upload_file(client, video_path, file_type="mp4")
    content = json.dumps({"file_key": file_key})
    if caption:
        await _create_message(client, chat_id, "text", json.dumps({"text": caption}))
    if reply_to:
        return await _reply_message(client, reply_to, "", msg_type="media", content_override=content)
    return await _create_message(client, chat_id, "media", content)


# ------------------------------------------------------------------
# 消息操作
# ------------------------------------------------------------------


async def edit_message(
    client: lark.Client,
    message_id: str,
    text: str,
) -> Dict[str, Any]:
    """编辑已发送的消息内容（飞书限 24 小时内）。"""

    def _do() -> Dict[str, Any]:
        content = json.dumps({"text": text})
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(
                PatchMessageRequestBody.builder()
                .content(content)
                .build()
            ).build()
        resp = client.im.v1.message.patch(req)
        if not resp.success():
            raise RuntimeError(f"编辑消息失败: code={resp.code}, msg={resp.msg}")
        return {"edited": True, "message_id": message_id}

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
        if not resp.success():
            raise RuntimeError(f"删除消息失败: code={resp.code}, msg={resp.msg}")
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
        if not resp.success():
            raise RuntimeError(f"转发消息失败: code={resp.code}, msg={resp.msg}")
        new_id = resp.data.message_id if resp.data else ""  # type: ignore[union-attr]
        return {"forwarded": True, "message_id": new_id}

    return await asyncio.to_thread(_do)


async def pin_message(client: lark.Client, message_id: str) -> Dict[str, Any]:
    """置顶消息。"""
    from lark_oapi.api.im.v1 import CreatePinRequest, CreatePinRequestBody

    def _do() -> Dict[str, Any]:
        body = CreatePinRequestBody.builder().message_id(message_id).build()
        req = CreatePinRequest.builder().request_body(body).build()
        resp = client.im.v1.pin.create(req)
        if not resp.success():
            raise RuntimeError(f"置顶消息失败: code={resp.code}, msg={resp.msg}")
        return {"pinned": True, "message_id": message_id}

    return await asyncio.to_thread(_do)


async def unpin_message(client: lark.Client, message_id: str) -> Dict[str, Any]:
    """取消置顶消息。"""
    from lark_oapi.api.im.v1 import DeletePinRequest

    def _do() -> Dict[str, Any]:
        req = DeletePinRequest.builder().message_id(message_id).build()
        resp = client.im.v1.pin.delete(req)
        if not resp.success():
            raise RuntimeError(f"取消置顶失败: code={resp.code}, msg={resp.msg}")
        return {"unpinned": True, "message_id": message_id}

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
        if not resp.success():
            raise RuntimeError(f"查询群信息失败: code={resp.code}, msg={resp.msg}")
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


async def get_chat_members(client: lark.Client, chat_id: str) -> Dict[str, Any]:
    """查询群聊成员列表。"""
    from lark_oapi.api.im.v1 import GetChatMembersRequest

    def _do() -> Dict[str, Any]:
        req = GetChatMembersRequest.builder() \
            .chat_id(chat_id) \
            .member_id_type("open_id") \
            .build()
        resp = client.im.v1.chat_members.get(req)
        if not resp.success():
            raise RuntimeError(f"查询成员失败: code={resp.code}, msg={resp.msg}")
        items = resp.data.items if resp.data else []  # type: ignore[union-attr]
        members = []
        for m in (items or []):
            members.append({
                "member_id": getattr(m, "member_id", ""),
                "name": getattr(m, "name", ""),
                "member_id_type": getattr(m, "member_id_type", ""),
            })
        return {"members": members, "count": len(members)}

    return await asyncio.to_thread(_do)


async def get_bot_info(client: lark.Client) -> Dict[str, str]:
    """获取 Bot 自身信息（open_id, app_name）。

    通过 lark-oapi 内部的 token 管理获取 tenant_access_token，
    然后直接调用 /open-apis/bot/v3/info/ 接口。
    """
    import httpx

    def _do() -> Dict[str, str]:
        config = client._config  # type: ignore[attr-defined]
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
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: code={resp.code}, msg={resp.msg}")
        msg_id = resp.data.message_id if resp.data else ""  # type: ignore[union-attr]
        return {"message_id": msg_id, "chat_id": chat_id}

    return await asyncio.to_thread(_do)


async def _reply_message(
    client: lark.Client,
    reply_to_id: str,
    text: str,
    *,
    msg_type: str = "text",
    content_override: Optional[str] = None,
) -> Dict[str, Any]:
    """回复消息（底层封装）。"""
    content = content_override or json.dumps({"text": text})

    def _do() -> Dict[str, Any]:
        req = ReplyMessageRequest.builder() \
            .message_id(reply_to_id) \
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            ).build()
        resp = client.im.v1.message.reply(req)
        if not resp.success():
            raise RuntimeError(f"飞书回复失败: code={resp.code}, msg={resp.msg}")
        msg_id = resp.data.message_id if resp.data else ""  # type: ignore[union-attr]
        return {"message_id": msg_id}

    return await asyncio.to_thread(_do)
