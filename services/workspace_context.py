"""工作区上下文注入 — 发送时把前端工作台状态（打开文件/选区/标签页）渲染为消息前缀块。

格式与回放清洗是同一约定（Codex IDE-context 式）：注入块以固定分隔符
``REQUEST_DELIMITER`` 结尾，历史渲染按分隔符取用户原文，避免注入内容在
对话历史里重复刷屏；跨端（WebUI 发送 / 历史清洗）共用此文件保证一致。

预算（对齐 Codex 常量）：选区 40k 字符 / 标签页 100 个 / 标签页合计 20k
字符，超出部分显式标注 ``[N omitted]``。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 注入块与用户原文的分隔符（历史清洗以此为锚取尾部）
REQUEST_DELIMITER = "## My request:"
# 注入块起始标记（清洗判定是否含注入块）
CONTEXT_HEADER = "# Context from workspace:"

MAX_ACTIVE_SELECTION_CHARS = 40_000
MAX_OPEN_TABS = 100
MAX_OPEN_TABS_CHARS = 20_000


def render_workspace_context(state: Dict[str, Any]) -> str:
    """把工作台状态快照渲染为注入前缀（无上下文内容时返回空串）。

    state 是前端上报的 ui_state：active_file / selection / open_tabs。
    """
    blocks: List[str] = []

    active_file = str(state.get("active_file") or "").strip()
    open_tabs = state.get("open_tabs")
    selection = state.get("selection")

    if active_file:
        blocks.append(f"## Active file: {active_file}")

    if isinstance(selection, dict):
        path = str(selection.get("path") or active_file or "")
        ranges = selection.get("ranges")
        if isinstance(ranges, list) and ranges:
            lines = ["## Active selection ranges:"]
            for r in ranges[:20]:
                if not isinstance(r, dict):
                    continue
                line = f"- {path}: line {r.get('start_line', '?')} to line {r.get('end_line', '?')}"
                lines.append(line)
            blocks.append("\n".join(lines))
        content = str(selection.get("content") or "")
        if content.strip():
            truncated = content[:MAX_ACTIVE_SELECTION_CHARS]
            if len(content) > MAX_ACTIVE_SELECTION_CHARS:
                truncated += "\n[selection truncated]"
            blocks.append(f"## Active selection of the file:\n{truncated}")

    if isinstance(open_tabs, list) and open_tabs:
        lines = ["## Open tabs:"]
        shown = open_tabs[:MAX_OPEN_TABS]
        omitted = len(open_tabs) - len(shown)
        used = 0
        for tab in shown:
            label = str(tab.get("label") or "")
            tab_path = str(tab.get("path") or "")
            row = f"- {label}: {tab_path}"
            if used + len(row) > MAX_OPEN_TABS_CHARS:
                break
            lines.append(row)
            used += len(row)
        if omitted > 0:
            lines.append(f"[{omitted} open tabs omitted.]")
        blocks.append("\n".join(lines))

    if not blocks:
        return ""
    return CONTEXT_HEADER + "\n\n" + "\n\n".join(blocks)


def inject_workspace_context(message: str, state: Dict[str, Any]) -> str:
    """把注入块拼到用户消息前（无上下文或消息已含分隔符时原样返回）。"""
    block = render_workspace_context(state)
    if not block:
        return message
    return f"{block}\n\n{REQUEST_DELIMITER}\n{message}"


def strip_workspace_context(message: str) -> str:
    """历史清洗：剥离注入块，只留用户原文（无注入块时原样返回）。"""
    if CONTEXT_HEADER not in message or REQUEST_DELIMITER not in message:
        return message
    _head, _sep, tail = message.rpartition(REQUEST_DELIMITER)
    return tail.lstrip("\n")
