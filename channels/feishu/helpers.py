"""飞书适配器工具函数。"""

from __future__ import annotations

import json
import re
from typing import List, Sequence

from .types import FeishuMention, PostContentResult

# ------------------------------------------------------------------
# 富文本 (post) 消息解析
# ------------------------------------------------------------------


def parse_post_content(raw_content: str) -> PostContentResult:
    """解析飞书富文本 (post) 消息 JSON，提取纯文本、图片 key 和 @信息。

    飞书 post 格式示例::

        {
          "zh_cn": {
            "title": "标题",
            "content": [
              [{"tag": "text", "text": "hello"},
               {"tag": "at", "user_id": "ou_xxx", "user_name": "Tom"},
               {"tag": "img", "image_key": "img_xxx"}]
            ]
          }
        }
    """
    result = PostContentResult()
    try:
        parsed = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        result.text = raw_content
        return result

    if not isinstance(parsed, dict):
        result.text = raw_content
        return result

    # 优先 zh_cn -> en_us -> 第一个语言
    locale_data = (
        parsed.get("zh_cn")
        or parsed.get("en_us")
        or next(iter(parsed.values()), None)
        if isinstance(parsed, dict) else None
    )
    if not isinstance(locale_data, dict):
        result.text = raw_content
        return result

    title = locale_data.get("title", "")
    content_blocks: list = locale_data.get("content", [])

    parts: List[str] = []
    if title:
        parts.append(title)

    for line_blocks in content_blocks:
        if not isinstance(line_blocks, list):
            continue
        line_parts: List[str] = []
        for element in line_blocks:
            if not isinstance(element, dict):
                continue
            tag = element.get("tag", "")
            if tag == "text":
                line_parts.append(str(element.get("text") or ""))
            elif tag == "a":
                href = str(element.get("href") or "")
                text = str(element.get("text") or href)
                line_parts.append(f"{text}({href})" if href else text)
            elif tag == "at":
                user_id = element.get("user_id", "")
                result.at_open_ids.append(user_id)
                line_parts.append(f"[at_uid:{user_id}]")
            elif tag == "img":
                image_key = element.get("image_key", "")
                if image_key:
                    result.image_keys.append(image_key)
            elif tag == "media":
                file_key = element.get("file_key", "")
                if file_key:
                    result.file_keys.append(file_key)
            elif tag == "md":
                line_parts.append(element.get("text", ""))
        if line_parts:
            parts.append("".join(line_parts))

    result.text = "\n".join(parts)
    return result


# ------------------------------------------------------------------
# 消息内容解析
# ------------------------------------------------------------------


def parse_text_content(raw_content: str) -> str:
    """解析飞书 text 消息 JSON，提取纯文本。"""
    try:
        parsed = json.loads(raw_content)
        return parsed.get("text", "") if isinstance(parsed, dict) else raw_content
    except (json.JSONDecodeError, TypeError):
        return raw_content


def parse_message_content(raw_content: str, msg_type: str) -> str:
    """根据消息类型解析内容为可读文本。"""
    if msg_type == "text":
        return parse_text_content(raw_content)
    if msg_type == "post":
        return parse_post_content(raw_content).text
    if msg_type == "image":
        return "[图片]"
    if msg_type == "file":
        try:
            parsed = json.loads(raw_content)
            return f"[文件: {parsed.get('file_name', 'unknown')}]"
        except (json.JSONDecodeError, TypeError):
            return "[文件]"
    if msg_type == "audio":
        return "[语音]"
    if msg_type in ("video", "media"):
        return "[视频]"
    if msg_type == "sticker":
        return "[表情]"
    if msg_type == "share_chat":
        return "[分享群聊]"
    if msg_type == "merge_forward":
        return "[合并转发消息]（入站合并转发内容飞书未开放展开接口，可用 feishu_get_chat_history 查看上下文）"
    if msg_type == "interactive":
        return _parse_interactive_text(raw_content)
    return raw_content


def _parse_interactive_text(raw_content: str) -> str:
    """从交互式卡片消息中提取文本。"""
    try:
        parsed = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return "[卡片消息]"

    elements = parsed.get("elements") or (parsed.get("body") or {}).get("elements") or []
    texts: List[str] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        tag = el.get("tag", "")
        if tag == "markdown":
            texts.append(el.get("content", ""))
        elif tag == "div":
            text_obj = el.get("text")
            if isinstance(text_obj, dict):
                texts.append(text_obj.get("content", ""))
    return "\n".join(texts).strip() or "[卡片消息]"


# ------------------------------------------------------------------
# @Bot 检测
# ------------------------------------------------------------------


def check_bot_mentioned(
    mentions: Sequence[FeishuMention] | None,
    bot_open_id: str,
) -> bool:
    """检查消息 mentions 中是否包含 Bot。"""
    if not mentions or not bot_open_id:
        return False
    return any(m.id.open_id == bot_open_id for m in mentions)


# ------------------------------------------------------------------
# Markdown 检测
# ------------------------------------------------------------------

_MD_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s", re.MULTILINE),          # 标题
    re.compile(r"\*\*[^*]+\*\*"),                     # 加粗
    re.compile(r"```"),                               # 代码块
    re.compile(r"`[^`\n]+`"),                         # 行内代码
    re.compile(r"^\s*[-*]\s", re.MULTILINE),          # 无序列表
    re.compile(r"^\s*\d+\.\s", re.MULTILINE),         # 有序列表
    re.compile(r"\[[^\]]+\]\([^)]+\)"),               # 链接
    re.compile(r"^\|.+\|$", re.MULTILINE),            # 表格
    re.compile(r"^>\s", re.MULTILINE),                # 引用
]


def looks_like_markdown(text: str) -> bool:
    """启发式判断文本是否含 Markdown 语法（含任一特征即视为富文本）。"""
    return any(p.search(text) for p in _MD_PATTERNS)


# ------------------------------------------------------------------
# 消息分块
# ------------------------------------------------------------------

FEISHU_TEXT_LIMIT = 4000


def chunk_text(text: str, limit: int = FEISHU_TEXT_LIMIT) -> List[str]:
    """将长文本按限制分块，尽量在换行处断开。"""
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # 优先在换行处断开
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ------------------------------------------------------------------
# 媒体 key 提取
# ------------------------------------------------------------------


def extract_media_key(raw_content: str, msg_type: str) -> dict[str, str]:
    """从消息 content JSON 中提取媒体 key。"""
    try:
        parsed = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return {}

    if msg_type == "image":
        return {"image_key": parsed.get("image_key", "")}
    if msg_type == "file":
        return {"file_key": parsed.get("file_key", ""), "file_name": parsed.get("file_name", "")}
    if msg_type == "audio":
        return {"file_key": parsed.get("file_key", "")}
    if msg_type in ("video", "media"):
        return {"file_key": parsed.get("file_key", ""), "image_key": parsed.get("image_key", "")}
    if msg_type == "sticker":
        return {"file_key": parsed.get("file_key", "")}
    return {}
