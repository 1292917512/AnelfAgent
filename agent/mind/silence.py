"""识别模型主动结束回复的沉默标记。"""

from __future__ import annotations

import re

# 沉默标记：AI 整条回复恰好是其中之一时视为"决定不回复"（精确匹配，
# 正文里提到这些词不会误杀——要求整条规范化后完全相等且有长度上限）
_SILENT_MARKERS = frozenset({"[silent]", "silent", "no_reply", "no reply"})
_SILENT_MAX_LEN = 64


def is_silent(text: str) -> bool:
    """沉默标记精确匹配：整条回复恰好是 [SILENT] 类标记才生效。"""
    if not text:
        return False
    # 规范化空白；剥离边缘标点但保留方括号结构（[SILENT] 与 SILENT 都接受）
    normalized = " ".join(text.split()).strip(" \t.,!?。，！？;；")
    if not normalized or len(normalized) > _SILENT_MAX_LEN:
        return False
    return normalized.lower() in _SILENT_MARKERS


# 沉默旁白：整条回复只是一个"沉默姿态"，
# 覆盖 *(silent)*、`silent`、(沉默)、*沉默*、🔇、裸 "." / "…" 等。
# 锚定整条字符串 + 长度上限，正文中包含这些词的正常回复不会被误杀。
_SILENCE_NARRATION_RE = re.compile(
    r"^[\s*_~`]*\(?\s*(silent|silence|no\s+response|no\s+reply)\s*\.?\)?[\s*_~`]*$"
    r"|[\U0001F507.…。]+",
    re.IGNORECASE,
)
# 中文沉默旁白：必须带包裹符号（括号/markdown 标记）才判定，裸词"沉默"可能是正常回答
_SILENCE_NARRATION_CN = frozenset({"沉默", "不回复", "不回应"})
_SILENCE_NARRATION_CN_WRAP = "*_~` \t（）()【】[]"


def is_silence_narration(text: str) -> bool:
    """检测幻觉性的"沉默旁白"文本（整条只是一个姿态标记，无实际内容）。"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _SILENT_MAX_LEN:
        return False
    if _SILENCE_NARRATION_RE.fullmatch(stripped):
        return True
    inner = stripped.strip(_SILENCE_NARRATION_CN_WRAP)
    return inner != stripped and inner in _SILENCE_NARRATION_CN


def should_suppress(text: str) -> bool:
    """判断整条正文是否表达不回复的意图。"""
    return is_silent(text) or is_silence_narration(text)
