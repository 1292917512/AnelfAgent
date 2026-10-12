"""完整冻结触发时的消息链，缓存分层标记只在最终发送边界剥离。

模型可见：继承原消息、工具调用及结果；不人为截断执行证据。
Token 影响：使用原对话的上下文窗口，溢出仍由统一压缩器处理。
缓存影响：保持原消息顺序、内容与分层；钩子指令追加到尾部。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def freeze_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """深拷贝消息及嵌套工具、多模态内容，隔离父对话的后续写入。"""
    return deepcopy([message for message in messages or [] if isinstance(message, dict)])
