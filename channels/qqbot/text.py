"""QQ 文本与提及编码，按目标和消息类型校验平台能力。

模型看到：统一提及标记不变，不支持的组合在发送前返回可修正的错误。
Token：只增加必要的错误说明，不增加模型调用。
缓存：仅处理出站消息，不修改模型上下文或稳定前缀。
"""

import html
import re
from bisect import bisect_right
from collections.abc import Iterator
from typing import Literal

from agent.channel.schemas import SegmentType, SendRequest, SendSegment
from core.tags import replace_tags, tag_label

from .events import Target, identifier

TextMode = Literal["text", "markdown", "media", "card"]
_TEXT_TYPES = {SegmentType.TEXT, SegmentType.AT}
_ATOMS = re.compile(
    r'<qqbot-(?:at-user id="[A-Za-z0-9_-]+"|at-everyone)\s*/>'
    r'|&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9A-Fa-f]+);'
)


def render_text(content: str, target: Target, mode: TextMode = "text") -> str:
    """保留正文格式，只把统一提及标记编码成当前消息支持的 QQ 标签。"""
    def replace(key: str, value: str) -> str:
        if key != "at_uid":
            return tag_label(key, value)
        if target.kind not in {"group", "channel"} or mode == "card":
            raise ValueError("当前 QQ 会话或图文卡片不支持 @ 提及；请使用普通正文或回复引用")
        if target.kind == "group" and mode != "markdown" and (value != "all" or mode != "text"):
            raise ValueError("QQ 群聊 @ 指定成员须使用 qqbot_send_markdown，content 保留 [at_uid:成员OpenID]；"
                             "媒体说明不支持群提及，请单独发送 Markdown。普通回复可不带 @，保留回复引用")
        return '<qqbot-at-everyone />' if value == "all" else f'<qqbot-at-user id="{identifier(value)}" />'

    text = html.escape(content, quote=False) if mode in {"text", "media"} else content
    return replace_tags(text, replace)


def text_segments(request: SendRequest) -> Iterator[SendSegment]:
    """同格式的 AT 与相邻文本组成正文；独立文本消息和媒体保持原有顺序。"""
    pending: list[SendSegment] = []
    mode = ""

    def flush() -> Iterator[SendSegment]:
        if any(segment.type == SegmentType.AT for segment in pending):
            content = "".join(segment.content if segment.type == SegmentType.TEXT else
                              tag_label("at_uid", segment.at_user_id) for segment in pending)
            yield SendSegment(content=content, parse_mode=mode)
        else:
            yield from pending

    for segment in request.segments:
        current = (request.parse_mode or segment.parse_mode or "").lower()
        if pending and (segment.type not in _TEXT_TYPES or current != mode):
            yield from flush()
            pending = []
        if segment.type in _TEXT_TYPES:
            pending.append(segment)
            mode = current
        else:
            yield segment
    if pending:
        yield from flush()


def text_chunks(content: str, max_length: int) -> list[str]:
    """按编码后的长度分段，保持提及标签、转义实体和正文空白完整。"""
    limit = max(max_length, 50)
    atoms = [(match.start(), match.end()) for match in _ATOMS.finditer(content)]
    if any(end - start > limit for start, end in atoms):
        raise ValueError("QQ 提及标签或转义实体超过单条消息长度，无法完整发送")
    starts = [start for start, _ in atoms]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        cut = min(start + limit, len(content))
        if cut < len(content):
            newline = content.rfind("\n", start, cut)
            if newline >= start + limit // 2:
                cut = newline + 1
            index = bisect_right(starts, cut - 1) - 1
            if index >= 0 and atoms[index][0] < cut < atoms[index][1]:
                cut = atoms[index][0]
        chunks.append(content[start:cut])
        start = cut
    return chunks
