"""朗读文本工程 — 流式断句与朗读清洗。

断句（SentenceSplitter）：LLM 增量文本流 → 完整句队列。
- 句末标点（。！？!?；;）与换行成句；英文缩写/小数点不成句（点前是字母
  且点后是字母/数字的 '.' 不切）；
- 首句快速切分：首句未产出前在软切点提前断句（窗口 6~36 字）——
  TTS 首请求不等待第一个完整句，开声延迟从整句缩到首个分句；
- 超长句强制次级切分：超过 tts_sentence_max_chars 时在软切点截断
  （听感停顿自然），无切点则硬切；
- 短尾合并：末尾碎片并入前句（避免单字成句的机械停顿）。

朗读清洗（strip_for_speech）：把书面文本转成"能听"的口播稿。
- markdown 剥离：代码块/行内代码、链接（留锚文本）、图片、标题/列表/
  引用标记、粗斜体标记、表格行、HTML 标签；
- 旁白剥离：星号/括号包裹的动作神态描写（*微笑*、（轻声））整段剔除
  （tts_strip_narration 可关）；
- CJK 空格规范化：中日韩字符之间的半角空格删除（"你 好"→"你好"），
  连续空白折叠（朗读时空格不产生停顿语义）；
- emoji 与符号段：纯符号行剔除，emoji 直接删除（不读"笑脸"）。
"""

from __future__ import annotations

import re
from typing import List, Optional

from core.config import get_config_bool, get_config_int

# 句末标点（中英文）
_SENT_END = "。！？!?；;"
# 次级切点（超长句的软切位置）
_SOFT_BREAK = "，、,：:—- "
# 首句快速切分窗口：缓冲超过上限即在窗口内软切（≥ 下限），开声不等整句；
# 窗口内无软切点不硬切（等句末标点或转入常规超长规则）
_FIRST_MAX_CHARS = 16
_FIRST_MIN_CHARS = 6

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>\n]+>")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_MARK_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.、)])\s+", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*\*|__)(.*?)\1")
_NARRATION_STAR_RE = re.compile(r"(?<!\*)\*[^*\n]{1,80}\*(?!\*)")
_NARRATION_PAREN_RE = re.compile(r"[（(][^（）()\n]{1,40}[）)]")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️\ufe0f]", flags=re.UNICODE)
_CJK_SPACE_RE = re.compile(r"([一-鿿぀-ヿ가-힯])\s+([一-鿿぀-ヿ가-힯])")
_BLANK_RUN_RE = re.compile(r"\s+")
_SYMBOL_ONLY_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)


def strip_for_speech(text: str) -> str:
    """书面文本 → 口播稿（markdown/旁白剥离 + CJK 空格规范化）。"""
    if not text.strip():
        return ""
    out = _CODE_FENCE_RE.sub("，代码略，", text)
    out = _INLINE_CODE_RE.sub("", out)
    out = _IMAGE_RE.sub("", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _TABLE_ROW_RE.sub("", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _LIST_MARK_RE.sub(r"\1", out)
    out = _TAG_RE.sub("", out)
    if get_config_bool("tts_strip_narration", True):
        # 旁白先于强调处理：单星号短段视为动作神态描写整段剔除
        out = _NARRATION_STAR_RE.sub("", out)
        out = _NARRATION_PAREN_RE.sub("", out)
    out = _EMPHASIS_RE.sub(r"\2", out)
    out = _EMOJI_RE.sub("", out)
    # CJK 空格规范化（反复消除链式空格：你 好 吗 → 你好吗）
    prev = None
    while prev != out:
        prev = out
        out = _CJK_SPACE_RE.sub(r"\1\2", out)
    lines = []
    for line in out.splitlines():
        line = _BLANK_RUN_RE.sub(" ", line).strip()
        if line and not _SYMBOL_ONLY_RE.match(line):
            lines.append(line)
    return " ".join(lines).strip()


class SentenceSplitter:
    """增量文本流 → 完整句（feed 推入增量，flush 收尾取残句）。"""

    def __init__(self) -> None:
        self._buf = ""
        self._emitted = 0
        """已产出句数——首句产出前启用快速切分（压开声延迟）。"""

    @staticmethod
    def _max_chars() -> int:
        return max(20, get_config_int("tts_sentence_max_chars", 120))

    def feed(self, delta: str) -> List[str]:
        """推入一段增量文本，返回新产出的完整句列表。"""
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> List[str]:
        """文本流结束：取出缓冲区残余（作为最后一句）。"""
        return self._drain(final=True)

    def reset(self) -> None:
        self._buf = ""
        self._emitted = 0

    def _drain(self, *, final: bool) -> List[str]:
        out: List[str] = []
        while True:
            cut = self._find_cut(final=final)
            if cut is None:
                break
            sentence = self._buf[:cut].strip()
            self._buf = self._buf[cut:]
            if sentence:
                out.append(sentence)
        if final and self._buf.strip():
            out.append(self._buf.strip())
            self._buf = ""
        self._emitted += len(out)
        return self._merge_tails(out)

    def _find_cut(self, *, final: bool) -> Optional[int]:
        """在缓冲区中找下一个切点（返回切点右端索引；无切点 None）。"""
        buf = self._buf
        for i, ch in enumerate(buf):
            if ch in _SENT_END:
                return i + 1
            if ch == ".":
                # 英文句号：前后都是字母/数字视为缩写/小数，不切
                left = buf[i - 1] if i > 0 else ""
                right = buf[i + 1] if i + 1 < len(buf) else ""
                if (left.isalnum() and right.isalnum()):
                    continue
                return i + 1
            if ch == "\n" and buf[:i].strip():
                return i + 1
        # 首句快速切分：首句未产出时在窗口内软切（早开声）
        if self._emitted == 0 and len(buf) > _FIRST_MAX_CHARS:
            hi = min(len(buf), _FIRST_MAX_CHARS + 20)
            for i in range(hi - 1, _FIRST_MIN_CHARS - 1, -1):
                if buf[i] in _SOFT_BREAK:
                    return i + 1
        # 超长软切（流式进行中也在软切点断句，控制单句延迟）
        max_chars = self._max_chars()
        if len(buf) > max_chars:
            for i in range(min(len(buf), max_chars + 20) - 1, 20, -1):
                if buf[i] in _SOFT_BREAK:
                    return i + 1
            if final or len(buf) > max_chars * 2:
                return max_chars
        return None

    @staticmethod
    def _merge_tails(sentences: List[str]) -> List[str]:
        """短尾合并：<4 字的碎片并入前句（单字成句听感机械）。"""
        if not sentences:
            return sentences
        merged: List[str] = []
        for sentence in sentences:
            if merged and len(sentence) < 4:
                merged[-1] = merged[-1] + sentence
            else:
                merged.append(sentence)
        return merged
