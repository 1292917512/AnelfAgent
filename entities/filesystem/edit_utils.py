"""edit_file 的纯函数算法库 — 移植自 Claude Code ``src/tools/FileEditTool/utils.ts``。

包含：弯引号归一化匹配、引号风格保持、逐行尾空格清理、删行特例、unified diff。
未移植 DESANITIZATIONS（ Anthropic API 特有的 token 消毒表，Anelf 不适用）。
"""

from __future__ import annotations

import difflib
import re
from typing import List, Optional, Tuple

LEFT_SINGLE_CURLY_QUOTE = "‘"
RIGHT_SINGLE_CURLY_QUOTE = "’"
LEFT_DOUBLE_CURLY_QUOTE = "“"
RIGHT_DOUBLE_CURLY_QUOTE = "”"


def normalize_quotes(s: str) -> str:
    """弯引号归一为直引号（用于匹配，不用于写盘）。"""
    return (s
            .replace(LEFT_SINGLE_CURLY_QUOTE, "'")
            .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
            .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
            .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"'))


def strip_trailing_whitespace(s: str) -> str:
    """逐行去除尾部空白，保留行尾符（CRLF/LF/CR）。"""
    parts = re.split(r"(\r\n|\n|\r)", s)
    out: List[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(re.sub(r"\s+$", "", part))
        else:
            out.append(part)
    return "".join(out)


def find_actual_string(file_content: str, search_string: str) -> Optional[str]:
    """在文件内容中定位 search_string 对应的原文。

    先精确匹配；失败则对弯引号归一后匹配，从原文切片返回真实文本
    （保留文件原有的弯引号，供后续精确替换）。
    """
    if search_string in file_content:
        return search_string
    normalized_search = normalize_quotes(search_string)
    normalized_file = normalize_quotes(file_content)
    idx = normalized_file.find(normalized_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None


def _is_opening_context(chars: List[str], index: int) -> bool:
    """开引号语境：行首/字符串首，或前字符为空白、开括号、破折号。"""
    if index == 0:
        return True
    prev = chars[index - 1]
    return prev in (" ", "\t", "\n", "\r", "(", "[", "{", "—", "–")


def _apply_curly_double_quotes(s: str) -> str:
    chars = list(s)
    out: List[str] = []
    for i, ch in enumerate(chars):
        if ch == '"':
            out.append(LEFT_DOUBLE_CURLY_QUOTE if _is_opening_context(chars, i)
                       else RIGHT_DOUBLE_CURLY_QUOTE)
        else:
            out.append(ch)
    return "".join(out)


def _apply_curly_single_quotes(s: str) -> str:
    chars = list(s)
    out: List[str] = []
    for i, ch in enumerate(chars):
        if ch == "'":
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i < len(chars) - 1 else ""
            if prev.isalpha() and nxt.isalpha():
                # 撇号（don't/it's）→ 右单弯引号
                out.append(RIGHT_SINGLE_CURLY_QUOTE)
            else:
                out.append(LEFT_SINGLE_CURLY_QUOTE if _is_opening_context(chars, i)
                           else RIGHT_SINGLE_CURLY_QUOTE)
        else:
            out.append(ch)
    return "".join(out)


def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """弯引号匹配成功时，把 new_string 的直引号还原为文件的弯引号风格。"""
    if old_string == actual_old_string:
        return new_string
    has_double = (LEFT_DOUBLE_CURLY_QUOTE in actual_old_string
                  or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string)
    has_single = (LEFT_SINGLE_CURLY_QUOTE in actual_old_string
                  or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string)
    result = new_string
    if has_double:
        result = _apply_curly_double_quotes(result)
    if has_single:
        result = _apply_curly_single_quotes(result)
    return result


def apply_edit_to_file(original: str, old_string: str, new_string: str,
                       replace_all: bool = False) -> str:
    """应用单次替换。

    删行特例：new_string 为空、old_string 不带尾换行、但 old_string+'\\n'
    存在时，连带删除该行换行（干净的整行删除）。
    """
    if new_string != "":
        return (original.replace(old_string, new_string) if replace_all
                else original.replace(old_string, new_string, 1))
    if not old_string.endswith("\n") and (old_string + "\n") in original:
        target = old_string + "\n"
        return (original.replace(target, "") if replace_all
                else original.replace(target, "", 1))
    return (original.replace(old_string, "") if replace_all
            else original.replace(old_string, "", 1))


def count_occurrences(content: str, needle: str) -> int:
    """统计 needle 在 content 中的出现次数（needle 非空）。"""
    if not needle:
        return 0
    count = 0
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            return count
        count += 1
        start = idx + len(needle)


def unified_diff(path: str, old_content: str, new_content: str,
                 context_lines: int = 3, max_chars: int = 4000) -> str:
    """生成 unified diff（供 UI 展示；不注入模型上下文）。"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}",
        n=context_lines,
    ))
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n... (diff 过长已截断)"
    return diff


def diff_stats(old_content: str, new_content: str) -> Tuple[int, int]:
    """统计增删行数（additions, removals）。"""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    additions = removals = 0
    for line in difflib.unified_diff(old_lines, new_lines, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            removals += 1
    return additions, removals
