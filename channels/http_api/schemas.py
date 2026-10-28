"""HTTP 门户的消息和控制请求；会话标识与标准频道 scope 使用同一构造规则。"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from agent.messages import build_entity_scope
from core.tags import tag_label

SkillName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?$")]


class SkillSelection(BaseModel):
    """外部协议使用技能名字段，由适配器转换内部标签。"""

    skills: list[SkillName] = Field(default_factory=list, max_length=8, description="显式加载正文的技能名，最多 8 个")

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    def tagged_content(self, content: str) -> str:
        """使用统一编码器生成内部标签，正文无需包含标签协议。"""
        prefix = "".join(tag_label("skill", name) for name in self.skills)
        return f"{prefix}\n{content}" if prefix else content


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


class ChatRequest(SessionTarget, SkillSelection):
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


class ContextRequest(SessionTarget, SkillSelection):
    """覆盖连接的唯一动态快照；空字符串清除，不改静态提示词。"""

    model_config = ConfigDict(extra="forbid")

    dynamic_context: str = Field(default="", max_length=16_000, description="实时环境快照，每次模型调用前刷新；与空 skills 一起清除")

    @model_validator(mode="after")
    def require_context(self) -> ContextRequest:
        if not self.model_fields_set.intersection({"dynamic_context", "skills"}):
            raise ValueError("至少提供 dynamic_context 或 skills")
        return self


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
