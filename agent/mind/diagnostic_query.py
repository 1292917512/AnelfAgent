"""消息内存窗口与执行诊断的只读联查，不复制完整会话或触发数据库读取。

模型可见：工具按需返回有界消息摘录及对应 scope 的执行事实。
Token 影响：默认最近三轮、每轮十二步和八条消息，无额外 LLM 调用。
缓存影响：不更新已读水位或任何上下文层，结果仅经工具返回。
"""

from __future__ import annotations

from typing import Any

from agent.messages import build_scope_id, parse_entity_scope
from agent.mind.diagnostics import PREVIEW_LIMIT, ExecutionDiagnostics, execution_diagnostics, preview
from agent.storage.sqlite_backend import SqliteBackend


def recent_diagnostics(
    sqlite: SqliteBackend | None, *, scope: str = "", turn_id: str = "",
    limit: int = 3, steps: int = 12, messages: int = 8,
    store: ExecutionDiagnostics = execution_diagnostics,
) -> dict[str, Any]:
    """返回同一时点的内存摘录与诊断；缺失和淘汰不等于没有消息或没有执行。"""
    result = store.snapshot(scope=scope, turn_id=turn_id, limit=limit, steps=steps)
    scopes = list(dict.fromkeys(
        [scope] if scope else [run["origin_scope"] or run["scope"] for run in result["runs"]]
    ))
    conversations = []
    for value in scopes:
        scope_type, adapter, base, session = parse_entity_scope(value)
        rows, loaded = (sqlite.peek_live_conversation(
            scope_type=scope_type, scope_id=build_scope_id(adapter, base, f"#{session}" if session else ""), limit=messages,
        ) if scope_type and sqlite else ([], False))
        conversations.append({"scope": value, "loaded": loaded, "messages": [
            {"id": str(row["id"]), "role": row["role"], "ts": row["ts_ns"] / 1_000_000_000,
             "preview": preview(row["content"]), "truncated": len(row["content"]) > PREVIEW_LIMIT} for row in rows
        ]})
    result.update(conversations=conversations, scopes=store.scopes(), notes=[
        "消息按提交 ID 排序；摘录来自已加载的会话内存窗口，未加载不补读数据库。",
        "轮次 duration_ms 为实际经过时间；并行工具、模型、钩子耗时不能相加当成总耗时。",
        "模型 preview 是生成文本，不代表已发送；仅 delivery 回执描述投递状态，缺失回执表示未观测。",
        "缓存缺数据或 cache_observable=false 时不可判断命中率；诊断仅覆盖思维执行事件，非全部供应商调用。",
        "执行摘要不含完整推理、系统提示词或媒体；省略步骤可提高 steps 查询，进程重启或淘汰后不可恢复。",
    ])
    return result
