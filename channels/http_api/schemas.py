"""HTTP 门户的消息和控制请求；会话标识与标准频道 scope 使用同一构造规则。"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from agent.messages import build_entity_scope


class SessionTarget(BaseModel):
    """HTTP 频道内的用户、群组与子会话目标，不能指定其他适配器。"""

    user_id: str = Field(default="api_user", min_length=1, max_length=128, description="稳定用户 ID，不含 #")
    group_id: str = Field(default="", max_length=128, description="群聊 ID，空表示私聊；不含 #，不能为保留值 0")
    session_id: str = Field(default="", max_length=128)

    @field_validator("user_id", "group_id")
    @classmethod
    def validate_identity(cls, value: str, info: ValidationInfo) -> str:
        """身份中的 # 会与子会话分隔符冲突；空白身份不具有稳定归属。"""
        if "#" in value or (value and not value.strip()):
            raise ValueError("user_id/group_id 不得包含 # 或仅由空白组成")
        if info.field_name == "group_id" and value == "0":
            raise ValueError("group_id=0 是框架保留的非群聊标识；私聊请省略 group_id")
        return value

    @property
    def chat_id(self) -> str:
        return self.group_id or self.user_id

    @property
    def channel_type(self) -> str:
        return "group" if self.group_id else "private"

    @property
    def scope(self) -> str:
        return build_entity_scope("group" if self.group_id else "user", "http_api", self.chat_id, self.session_id)


class ImageItem(BaseModel):
    url: str = Field(default="", max_length=2048)
    base64: str = ""
    mime_type: str = Field(default="image/jpeg", max_length=100)


class MediaItem(BaseModel):
    """音频、视频或文件的 base64 附件。"""

    kind: str = Field(default="file", max_length=16)
    base64: str = ""
    mime_type: str = Field(default="", max_length=100)
    file_name: str = Field(default="", max_length=255)


class ChatRequest(SessionTarget):
    message: str = Field(default="", max_length=256_000)
    user_name: str = Field(default="", max_length=128)
    message_id: str = Field(default="", max_length=128)
    reply_to_id: str = Field(default="", max_length=128)
    reply_content: str = Field(default="", max_length=2000)
    to_me: bool = True
    kind: str = "chat"
    images: list[ImageItem] = Field(default_factory=list)
    media: list[MediaItem] = Field(default_factory=list)
    callback_url: str = Field(default="", max_length=2048)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12], min_length=1, max_length=128)
    async_mode: bool = False


class ChatResponse(BaseModel):
    request_id: str
    status: str = "ok"
    reply: str = ""
    error: str = ""


class VisionPushRequest(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    image_base64: str
    mime_type: str = Field(default="image/png", max_length=100)
    captured_at: float | None = None


class ExecutionRequest(SessionTarget):
    """停止指定会话或精确轮次；继续以一条新消息进入原会话。"""

    action: Literal["stop", "resume"] = "stop"
    run_id: str = Field(default="", max_length=128)
    reason: str = Field(default="HTTP 调用方停止执行", max_length=500)
    message: str = Field(
        default="继续刚才中止的任务。已完成的操作不要重复执行；结果不确定的操作先核对实际状态。",
        min_length=1, max_length=256_000)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128)
