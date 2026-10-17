"""主回复工具日志 — 副作用可见性的崩溃尾部账本。

REPLY 模式每个执行过的工具调用追加一行（名称/参数摘要/结果摘要/状态），
回复正常收束随检查点一并清除；进程崩溃残留的行由 crash_recovery 读取，
把"上次执行到哪"渲染进中断元消息——执行摘要只在收尾落库，崩溃即丢失，
没有这份账本，重启后模型会对已发生的操作盲试（重复执行写操作是真实风险）。

记录的是管线加工后的结果（已脱敏/截断），恢复注入时不再二次加工。
生命周期与 reply_checkpoints 同构：登记于回复进入、清除于回复收束、
崩溃残留由启动恢复消费——journal 行只在"存在残留检查点"时才被读取。
"""
from __future__ import annotations

from typing import Any, Optional

from core.log import log

_ARG_HEAD_CHARS = 400
_RESULT_HEAD_CHARS = 240
_RENDER_MAX_ROWS = 12
_RENDER_ARG_CHARS = 80
_RENDER_RESULT_CHARS = 120


class ReplyToolJournal:
    """scope 级工具调用追加账本（sqlite reply_tool_journal 表的薄封装）。"""

    def __init__(self, sqlite: Any, scope_key: str) -> None:
        self._sqlite = sqlite
        self._scope_key = scope_key

    async def record(
            self, tool: str, arguments: str, result: str, *, status: str = "ok",
    ) -> None:
        """追加一行（fail-open：落账失败只记日志，绝不影响工具轮）。"""
        if not self._scope_key:
            return
        try:
            await self._sqlite.append_reply_tool_row(
                self._scope_key, tool,
                arguments[:_ARG_HEAD_CHARS], result[:_RESULT_HEAD_CHARS], status,
            )
        except Exception as exc:
            log(f"工具日志落账失败（已忽略）: {exc}", "DEBUG", tag="思维")

    async def render(self) -> str:
        """渲染已执行操作清单（供崩溃恢复注入；无记录返回空串）。"""
        try:
            rows = await self._sqlite.load_reply_tool_journal(self._scope_key)
        except Exception as exc:
            log(f"工具日志读取失败（已忽略）: {exc}", "DEBUG", tag="启动")
            return ""
        if not rows:
            return ""
        lines = ["中断前本轮已执行的操作："]
        for i, row in enumerate(rows[:_RENDER_MAX_ROWS], 1):
            tool = row.get("tool_name", "")
            args = row.get("arguments", "") or ""
            args_brief = args[:_RENDER_ARG_CHARS] + ("…" if len(args) > _RENDER_ARG_CHARS else "")
            status = row.get("status", "ok")
            if status == "ok":
                lines.append(f"{i}. {tool}({args_brief})")
            else:
                head = (row.get("result_head", "") or "")[:_RENDER_RESULT_CHARS]
                lines.append(f"{i}. {tool}({args_brief}) → {status}: {head}")
        hidden = len(rows) - _RENDER_MAX_ROWS
        if hidden > 0:
            lines.append(f"…另有 {hidden} 个操作未列出")
        return "\n".join(lines)

    async def clear(self) -> None:
        """回复收束清除（fail-open）。"""
        if not self._scope_key:
            return
        try:
            await self._sqlite.clear_reply_tool_journal(self._scope_key)
        except Exception as exc:
            log(f"工具日志清除失败（已忽略）: {exc}", "DEBUG", tag="思维")


def build_reply_journal(mind: Any, scope_key: str) -> Optional[ReplyToolJournal]:
    """按 mind 的会话路由构建日志（存储缺失时返回 None，零成本退化）。"""
    if not scope_key:
        return None
    try:
        sqlite = mind.conversation_data.router.sqlite
    except Exception:
        return None
    if sqlite is None:
        return None
    return ReplyToolJournal(sqlite, scope_key)
