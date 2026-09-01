"""edit_file 的纯函数算法库 — 移植自 Claude Code ``src/tools/FileEditTool/utils.ts``。

包含：四级递降容错匹配（精确 → 弯引号归一 → 行尾空白 → Unicode 标点/空白归一，
对齐 codex apply-patch seek_sequence / git apply 模糊行为）、引号风格保持、
逐行尾空格清理、删行特例、unified diff。
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

# Unicode 归一化映射（对齐 codex seek_sequence::normalise，模仿 git apply 的模糊行为）。
# 模型按看到的显示字符写 old_string，但原文可能是 NBSP/全角空格/连字符等宽字符变体。
_UNICODE_NORMALIZE_TABLE = str.maketrans({
    # 各类破折号/减号 → ASCII 连字符
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    # 单弯引号/低引号 → ASCII 撇号
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    # 双弯引号/低引号 → ASCII 引号
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # NBSP 与各类 Unicode 空格 → ASCII 空格
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", "　": " ",
})


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


def _strip_trailing_ws_lines(s: str) -> str:
    """逐行去除尾部空白并统一按 LF 连接（匹配用归一视图，不用于写盘）。"""
    return "\n".join(line.rstrip() for line in s.splitlines())


def _find_line_span(content: str, norm_search: str, view) -> Optional[str]:
    """按行对齐的归一查找：对归一视图逐行扫描，返回原文中对应的切片。

    view(str) -> str 把一行映射为归一形式；两端的归一视图（content/search）
    用同一个函数生成，行数一致，因此命中段可以直接按行下标回切原文。
    """
    file_lines = content.split("\n")
    needle_lines = norm_search.split("\n")
    if not needle_lines or len(needle_lines) > len(file_lines):
        return None
    target = tuple(view(line) for line in needle_lines)
    for i in range(0, len(file_lines) - len(needle_lines) + 1):
        if tuple(view(line) for line in file_lines[i:i + len(needle_lines)]) == target:
            return "\n".join(file_lines[i:i + len(needle_lines)])
    return None


def find_actual_string(file_content: str, search_string: str) -> Optional[str]:
    """在文件内容中定位 search_string 对应的原文。

    四级递降容错（严格度递减，命中即返回原文切片，供精确替换）：
    1. 精确子串；
    2. 弯引号归一后匹配（原文含弯引号时保留原样返回）；
    3. 逐行行尾空白归一（模型看不到 read_file 输出中的行尾空格/Tab，
       old_string 漏掉即可命中；**只容忍行尾，不放松缩进**）；
    4. Unicode 标点/空白归一（NBSP、全角空格、各类破折号、弯引号 → ASCII，
       同样不放松缩进）。
    """
    if search_string in file_content:
        return search_string
    normalized_search = normalize_quotes(search_string)
    normalized_file = normalize_quotes(file_content)
    idx = normalized_file.find(normalized_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    found = _find_line_span(file_content, search_string, lambda s: s.rstrip())
    if found is not None:
        return found
    return _find_line_span(
        file_content, search_string,
        lambda s: s.translate(_UNICODE_NORMALIZE_TABLE),
    )


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
