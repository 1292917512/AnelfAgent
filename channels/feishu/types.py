"""飞书适配器类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FeishuSenderId:
    """飞书发送者 ID 集合。"""

    open_id: str = ""
    user_id: str = ""
    union_id: str = ""


@dataclass
class FeishuMention:
    """飞书消息中的 @提及信息。"""

    key: str = ""
    name: str = ""
    id: FeishuSenderId = field(default_factory=FeishuSenderId)
    tenant_key: str = ""


@dataclass
class FeishuBotInfo:
    """Bot 身份信息（启动时获取）。"""

    open_id: str = ""
    app_name: str = ""


@dataclass
class PostContentResult:
    """富文本 (post) 消息解析结果。"""

    text: str = ""
    image_keys: List[str] = field(default_factory=list)
    file_keys: List[str] = field(default_factory=list)
    at_open_ids: List[str] = field(default_factory=list)


@dataclass
class FeishuMediaInfo:
    """下载后的媒体文件信息。"""

    path: str = ""
    content_type: str = ""
    placeholder: str = ""
    file_name: str = ""
