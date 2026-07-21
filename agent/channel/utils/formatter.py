"""通用文本格式化工具 — 跨频道复用。

提供 Markdown → 各平台原生格式的转换，借鉴 openclaw format.ts
和 channels/telegram/format.py。

目前支持：
- markdown_to_plain：去除所有 Markdown 标记（兜底）
- markdown_to_html：转换为 Telegram/Discord 风格的 HTML 子集
- chunk_text：按段落边界分割长文本（通用）
- normalize_at_mentions：处理 [at_uid:xxx] 标记

后续可扩展：
- markdown_to_lark_post：飞书富文本
- markdown_to_onebot_cq：QQ CQ 码
"""

from __future__ import annotations

import html
import re
from typing import List


# ----------------------------------------------------------------------
# 通用长文本分割
# ----------------------------------------------------------------------


def chunk_text(text: str, max_len: int = 4000) -> List[str]:
    """按段落边界分割长文本，避免截断句子。

    Args:
        text: 原始文本
        max_len: 单段最大长度

    Returns:
        分割后的段落列表（顺序保留）
    """
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 优先在换行处断开
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            # 其次在句号/感叹号/问号处断开
            for sep in ("。", "！", "？", ". ", "! ", "? "):
                cut = text.rfind(sep, 0, max_len)
                if cut > 0:
                    cut += len(sep)
                    break
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ----------------------------------------------------------------------
# @ 提及标记处理
# ----------------------------------------------------------------------


_AT_PATTERN = re.compile(r"\[at_uid:([^\]]+)\]")


def normalize_at_mentions(text: str, format_fn=None) -> str:
    """将 [at_uid:xxx] 转换为指定格式。

    Args:
        text: 原始文本
        format_fn: 接收 user_id 返回字符串的函数；默认转为 "@uid"
    """
    def _default(uid: str) -> str:
        if uid == "all":
            return "@全体成员"
        return f"@{uid}"

    fn = format_fn or _default
    return _AT_PATTERN.sub(lambda m: fn(m.group(1)), text)


# ----------------------------------------------------------------------
# Markdown → 纯文本（兜底）
# ----------------------------------------------------------------------


_MD_PATTERNS = [
    (re.compile(r"```(\w*)\n(.*?)```", re.DOTALL), r"\2"),  # 代码块
    (re.compile(r"`([^`\n]+)`"), r"\1"),  # 行内代码
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),  # 粗体
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"), r"\1"),  # 斜体
    (re.compile(r"~~(.+?)~~"), r"\1"),  # 删除线
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\1 (\2)"),  # 链接
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),  # 标题
    (re.compile(r"^>\s?", re.MULTILINE), ""),  # 引用
]


def markdown_to_plain(text: str) -> str:
    """将 Markdown 转为纯文本（去除所有标记）。"""
    if not text:
        return ""
    result = text
    for pattern, repl in _MD_PATTERNS:
        result = pattern.sub(repl, result)
    return result.strip()


# ----------------------------------------------------------------------
# Markdown → HTML（Telegram 风格）
# ----------------------------------------------------------------------


_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BLOCKQUOTE_LINE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)


def markdown_to_html(text: str) -> str:
    """将 Markdown 转为 Telegram/Discord 支持的 HTML 子集。

    支持：b, i, u, s, code, pre, a, blockquote
    """
    if not text:
        return ""
    # 处理 @ 标记（在转义之前）
    text = normalize_at_mentions(text)
    # HTML 转义
    text = html.escape(text)
    # 代码块（先处理，避免内部内容被其他规则匹配）
    text = _CODE_BLOCK_RE.sub(
        lambda m: f"<pre>{html.escape(m.group(2))}</pre>", text,
    )
    # 行内代码
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    # 粗体 / 斜体 / 删除线
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    # 链接
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    # 引用
    text = _BLOCKQUOTE_LINE_RE.sub(r"<blockquote>\1</blockquote>", text)
    return text.strip()


def plain_fallback_html(text: str) -> str:
    """纯文本 → HTML 转义（HTML 解析失败时兜底）。"""
    return html.escape(text)


# ----------------------------------------------------------------------
# 分块 + 转换工具
# ----------------------------------------------------------------------


def chunk_and_convert(
    text: str,
    *,
    max_len: int = 4000,
    convert_fn=None,
) -> List[dict]:
    """分块并可选转换。返回 [{"text": str, "converted": str}, ...]"""
    chunks = chunk_text(text, max_len)
    fn = convert_fn or (lambda x: x)
    return [{"text": c, "converted": fn(c)} for c in chunks]
