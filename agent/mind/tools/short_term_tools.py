"""短期记忆管理工具：AI 自清理已完成的临时提醒，避免堆积。

短期记忆（temporary clip）是任务指令/定时提醒/系统推送等一次性提示的投递区，
随每轮上下文注入。条目对应的事项完成后，AI 应主动清理，防止桶内堆积稀释注意力。

通过 deferred_tool 模式注册（group="thinking"），bootstrap 阶段激活。
"""

from __future__ import annotations

import json

from agent.mind.tools.session_tools import _current_scope, _system_not_ready
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import deferred_tool

from .ports import mind_port


@deferred_tool(
    group="thinking",
    tags=["always"],
    source="mind.short_term",
    description="列出当前会话的短期记忆条目（带索引）。短期记忆是临时提醒区"
    "（任务指令/定时提醒/系统推送等），对应事项完成后应调用 "
    "remove_short_term_memory 清理，避免堆积。",
)
async def list_short_term_memory() -> str:
    """列出当前会话短期记忆（带索引）。"""
    if not mind_port.bound:
        return _system_not_ready()
    pfc = mind_port.get().pfc
    clips = pfc.get_temporary(_current_scope())
    items = [
        {"index": i, "role": str(c.get("role", "")), "content": str(c.get("content", ""))[:200]}
        for i, c in enumerate(clips)
    ]
    return json.dumps(
        {
            "count": len(items),
            "items": items,
            "hint": "已完成事项对应的条目用 remove_short_term_memory(index) 清理；索引与上下文中短期记忆出现顺序一致（0 起）。",
        },
        ensure_ascii=False,
    )


@deferred_tool(
    group="thinking",
    tags=["always"],
    source="mind.short_term",
    description="按索引删除一条短期记忆（索引见 list_short_term_memory，与上下文中短期记忆出现顺序一致，0 起）。"
    "任务指令/提醒对应的事项完成后，用它及时清理。",
)
async def remove_short_term_memory(index: int) -> str:
    """按视图索引删除一条短期记忆。"""
    if not mind_port.bound:
        return _system_not_ready()
    pfc = mind_port.get().pfc
    if not pfc.delete_temporary_in_scope(_current_scope(), int(index)):
        return tool_error(
            f"短期记忆索引无效: {index}",
            cause=ErrorCause.NOT_FOUND,
            retryable=False,
            hint="先调用 list_short_term_memory 获取当前有效索引",
        )
    return json.dumps(
        {"removed": index, "remaining": len(pfc.get_temporary(_current_scope()))},
        ensure_ascii=False,
    )


@deferred_tool(
    group="thinking",
    tags=["always"],
    source="mind.short_term",
    description="清空当前会话可见的全部短期记忆。仅当确认所有临时提醒都已处理完、需要整体清零时使用；"
    "日常清理优先用 remove_short_term_memory 逐条删除。",
)
async def clear_short_term_memory() -> str:
    """清空当前会话视图覆盖的短期记忆。"""
    if not mind_port.bound:
        return _system_not_ready()
    cleared = mind_port.get().pfc.clear_temporary_in_scope(_current_scope())
    return json.dumps({"cleared": cleared}, ensure_ascii=False)
