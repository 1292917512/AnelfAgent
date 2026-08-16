"""worker 出站消息构建的纯函数层（零第三方依赖，主环境可测试）。

职责：
- ``[at_uid:x]`` 标签解析：OneBot 平台转 at 消息段（支持 @全体成员），
  其余平台降级为纯文本 ``@x``；
- 媒体源解析：URL / file_id 直传，本地路径读取后 base64 内联
  （对齐直连 QQ 频道的沙箱安全做法，含大小上限）。
"""

from __future__ import annotations

import base64
import os
import re
from typing import List, Optional, Tuple

# [at_uid:x] 标签（与直连 QQ 频道一致的约定格式）
_AT_TAG_RE = re.compile(r"\[at_uid:([^\]]+)\]")

# 本地文件 base64 内联上限（与直连 QQ 频道一致：100MB）
MAX_INLINE_BYTES = 100 * 1024 * 1024

# at 段（"at", qq）或文本段（"text", 内容）
OutSegment = Tuple[str, str]


def split_at_segments(text: str) -> List[OutSegment]:
    """把含 [at_uid:x] 标签的文本拆分为 at/text 有序段列表。

    Example:
        >>> split_at_segments("看这里[at_uid:123] 和 [at_uid:all]")
        [("text", "看这里"), ("at", "123"), ("text", " 和 "), ("at", "all")]
    """
    segments: List[OutSegment] = []
    cursor = 0
    for match in _AT_TAG_RE.finditer(text):
        if match.start() > cursor:
            segments.append(("text", text[cursor:match.start()]))
        segments.append(("at", match.group(1)))
        cursor = match.end()
    if cursor < len(text):
        segments.append(("text", text[cursor:]))
    return segments


def plain_at_text(text: str) -> str:
    """[at_uid:x] → 纯文本 @x（非 OneBot 平台降级用；all → @全体成员）。"""
    def _replace(match: "re.Match[str]") -> str:
        uid = match.group(1)
        return "@全体成员" if uid == "all" else f"@{uid}"

    return _AT_TAG_RE.sub(_replace, text)


def looks_like_url(source: str) -> bool:
    """是否为可直传的 URL 源。"""
    return source.startswith(("http://", "https://", "file://", "base64://"))


def resolve_media_source(source: str) -> str:
    """解析媒体源为 OneBot 可用的 file 字段值。

    - URL / file:// / base64:// 直传；
    - file_id（如 ``ABC.image``，无路径分隔符的协议端标识）直传；
    - 本地路径读取后 ``base64://`` 内联（超过上限抛 ValueError）；
    - 文件不存在抛 FileNotFoundError。
    """
    if looks_like_url(source):
        return source
    if not os.path.exists(source):
        if os.path.basename(source) == source:
            # 无分隔符 → 协议端 file_id（NapCat/Lagrange 服务端可自行解析）
            return source
        raise FileNotFoundError(f"媒体文件不存在: {source}")

    size = os.path.getsize(source)
    if size > MAX_INLINE_BYTES:
        raise ValueError(f"媒体文件超过 {MAX_INLINE_BYTES // (1024 * 1024)}MB 上限: {source}")
    with open(source, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"base64://{encoded}"


def file_display_name(source: str, override: Optional[str] = None) -> str:
    """文件发送时的展示名（覆盖值 > 源 basename > file_id 原样）。"""
    if override:
        return override
    name = os.path.basename(source)
    return name if name else source
