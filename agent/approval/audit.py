"""工具权限决策的持久审计；写入失败不改变已经作出的执行结论。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.log import log
from core.sanitizer import sanitize_text

if TYPE_CHECKING:
    from agent.storage.sqlite_backend import SqliteBackend

# 审计记录的 args_json 序列化上限（参数已在 gate 层脱敏，此处只防超大正文）
_ARGS_JSON_MAX_CHARS = 2000

# 仅审计的 outcome 词表（写入口校验，防拼写漂移）
AUDITED_OUTCOMES = frozenset({
    "denied", "guardian_approved", "guardian_denied", "guardian_bypass", "permission_error",
})


def _audit_sink() -> SqliteBackend | None:
    """取 sqlite 审计写入面（runtime 未就绪返回 None）。"""
    try:
        from agent.runtime.singleton import get_runtime
        runtime = get_runtime()
        return runtime.data_center.router.sqlite if runtime is not None else None
    except Exception:
        return None


async def record_decision(
    *,
    tool_name: str,
    outcome: str,
    decided_by: str = "",
    reason: str = "",
    channel_id: str = "",
    chat_id: str = "",
    user_id: str = "",
    risk_level: str = "",
    matched_rule: str = "",
    tool_args: Optional[Dict[str, Any]] = None,
) -> None:
    """追加一条审批决策审计（fail-open，调用方无需捕获异常）。

    outcome 必须在 AUDITED_OUTCOMES 内；超出词表说明调用方语义漂移，
    记 WARNING 后丢弃（宁缺毋错，防账本混入未定义语义）。
    """
    if outcome not in AUDITED_OUTCOMES:
        log(f"审批审计丢弃未知 outcome: {outcome} ({tool_name})", "WARNING", tag="权限")
        return
    sink = _audit_sink()
    if sink is None:
        return
    args_json = ""
    if tool_args:
        try:
            args_json = json.dumps(tool_args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args_json = ""
        args_json = args_json[:_ARGS_JSON_MAX_CHARS]
    record = {
        "ts_ns": None,  # 由 sqlite 侧补当前时间
        "tool_name": tool_name,
        "outcome": outcome,
        "decided_by": decided_by,
        "reason": sanitize_text(str(reason or ""))[:500],
        "channel_id": channel_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "risk_level": risk_level,
        "matched_rule": matched_rule,
        "args_json": args_json,
    }
    try:
        await sink.append_approval_audit(record)
    except Exception as exc:
        log(f"审批审计写入失败（已忽略）: {exc}", "WARNING", tag="权限")


async def list_history(
    limit: int = 50, offset: int = 0, tool_name: str = "",
) -> List[Dict[str, Any]]:
    """按时间倒序分页读取审计记录（web 历史页数据源）。"""
    sink = _audit_sink()
    if sink is None:
        return []
    try:
        return await sink.list_approval_audit(limit, offset, tool_name)
    except Exception as exc:
        log(f"审批审计读取失败: {exc}", "WARNING", tag="权限")
        return []


async def stats() -> Dict[str, Any]:
    """审计聚合统计（统计页数据源；runtime 未就绪返回空聚合）。"""
    sink = _audit_sink()
    if sink is None:
        return {"total": 0, "by_outcome": {}}
    try:
        return await sink.approval_audit_stats()
    except Exception as exc:
        log(f"审批审计统计失败: {exc}", "WARNING", tag="权限")
        return {"total": 0, "by_outcome": {}}
