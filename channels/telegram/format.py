"""Markdown → Telegram HTML 格式化。

Telegram 支持的 HTML 子集：
  <b>, <i>, <u>, <s>, <code>, <pre>, <a>, <blockquote>, <tg-spoiler>

参照 openclaw format.ts 的转换逻辑。
"""

from __future__ import annotations

import html
import re
from typing import List


def markdown_to_telegram_html(text: str) -> str:
    """将 Markdown 文本转换为 Telegram 支持的 HTML 子集。"""
    if not text:
        return ""
    # 先处理 @ 格式（在 escape 之前），避免 @ 中的特殊字符被转义
    text = _convert_at_mentions(text)
    text = _escape_special_chars(text)
    text = _convert_code_blocks(text)
    text = _convert_inline_code(text)
    text = _convert_bold(text)
    text = _convert_italic(text)
    text = _convert_strikethrough(text)
    text = _convert_links(text)
    text = _convert_blockquotes(text)
    return text.strip()


def chunk_text(text: str, max_len: int = 4096) -> List[str]:
    """按段落边界分割长文本。"""
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def chunk_html_text(text: str, max_len: int = 4096) -> List[dict]:
    """分块并转换为 HTML，返回 [{html, text}]。"""
    raw_chunks = chunk_text(text, max_len)
    return [
        {"html": markdown_to_telegram_html(c), "text": c}
        for c in raw_chunks
    ]


def plain_fallback(text: str) -> str:
    """HTML 解析失败时的纯文本回退。"""
    return html.escape(text)


# ------------------------------------------------------------------
# 内部转换函数
# ------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(
    r"```(\w*)\n(.*?)```", re.DOTALL,
)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BLOCKQUOTE_LINE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_AT_PATTERN_RE = re.compile(r'\[at_uid:([^\]]+)\]')

# 用于保护代码块/行内代码中的内容不被二次转换
_PLACEHOLDER_PREFIX = "\x00CB"
_placeholders: List[str] = []


def _convert_at_mentions(text: str) -> str:
    """将 [at_uid:xxx] 转换为 Telegram @ 链接。"""
    def replacer(match: re.Match) -> str:
        uid = match.group(1)
        if uid == "all":
            return ""
        return f'<a href="tg://user?id={uid}">{html.escape(uid)}</a>'

    return _AT_PATTERN_RE.sub(replacer, text)


def _escape_special_chars(text: str) -> str:
    """对 HTML 特殊字符转义（但保留 Markdown 语法字符）。"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _convert_code_blocks(text: str) -> str:
    def _replace(m: re.Match) -> str:
        lang = m.group(1)
        code = m.group(2).rstrip("\n")
        if lang:
            return f'<pre><code class="language-{lang}">{code}</code></pre>'
        return f"<pre>{code}</pre>"
    # 还原 > 在代码块中的转义
    def _replace_with_restore(m: re.Match) -> str:
        result = _replace(m)
        return result.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return _CODE_BLOCK_RE.sub(_replace_with_restore, text)


def _convert_inline_code(text: str) -> str:
    def _replace(m: re.Match) -> str:
        code = m.group(1)
        return f"<code>{code}</code>"
    return _INLINE_CODE_RE.sub(_replace, text)


def _convert_bold(text: str) -> str:
    return _BOLD_RE.sub(r"<b>\1</b>", text)


def _convert_italic(text: str) -> str:
    return _ITALIC_RE.sub(r"<i>\1</i>", text)


def _convert_strikethrough(text: str) -> str:
    return _STRIKE_RE.sub(r"<s>\1</s>", text)


def _convert_links(text: str) -> str:
    return _LINK_RE.sub(r'<a href="\2">\1</a>', text)


def _convert_blockquotes(text: str) -> str:
    lines = text.split("\n")
    result: List[str] = []
    in_quote = False
    quote_lines: List[str] = []

    for line in lines:
        m = _BLOCKQUOTE_LINE_RE.match(line)
        if m:
            if not in_quote:
                in_quote = True
                quote_lines = []
            quote_lines.append(m.group(1))
        else:
            if in_quote:
                result.append(f"<blockquote>{chr(10).join(quote_lines)}</blockquote>")
                in_quote = False
                quote_lines = []
            result.append(line)

    if in_quote:
        result.append(f"<blockquote>{chr(10).join(quote_lines)}</blockquote>")

    return "\n".join(result)
