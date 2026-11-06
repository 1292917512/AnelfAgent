"""持久化媒体消息：实时事件与历史展示共享同一份引用。

模型可见：发送工具回执包含媒体类型、可访问地址与关联 ID；不注入附件二进制。
Token 影响：每次媒体回执增加少量结构化字段，历史正文仍为说明文字。
缓存影响：仅工具结果变化，展示元数据不进入模型历史与稳定前缀。
"""

from typing import Literal

from pydantic import BaseModel, Field


class MediaMessage(BaseModel):
    """单次媒体投递的展示信息；cid 用于归并实时消息与持久化历史。"""

    cid: str = Field(min_length=1)
    media_type: Literal["image", "voice", "audio", "video", "file"]
    url: str = Field(min_length=1)
    caption: str = ""
