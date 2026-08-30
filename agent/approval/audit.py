"""审批审计持久化 — 决策落库与信任计数的唯一数据面。

职责（对齐 dsh approval/asked + approval/decided 持久审计对的结论语义）：
- 所有**非默认放行**的审批决策追加写入 ``approval_audit`` 表：
  人工批准/拒绝/取消/超时、规则拒绝、信任阈值放行、超时放行。
  常态规则放行（rule_allow）不记录——高频无信息量，会把账本刷成噪音。
- ``trust_after_n_approvals`` 的信任计数从账本统计（outcome=approved 的
  累计次数）——重启不再从零重数；信任放行本身（trusted）不计入 approved，
  避免自动放行自我强化信任。

全部 fail-open：审计写失败只记日志，绝不影响审批决策本身；runtime 未就绪
（测试/早期启动）时静默跳过。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from core.log import log
from core.async_helper import spawn

# 审计记录的 args_json 序列化上限（参数已在 gate 层脱敏，此处只防超大正文）
_ARGS_JSON_MAX_CHARS = 2000

# 仅审计的 outcome 词表（写入口校验，防拼写漂移）
AUDITED_OUTCOMES = frozenset({
    "approved",    # 人工批准
    "denied",      # 人工拒绝 / 规则拒绝 / 超时拒绝
    "cancelled",   # 取消（发送提示失败 / agent 中断）
    "expired",     # 超时未决策
    "trusted",     # 信任阈值自动放行（不计入 approved 计数）
    "timeout_allow",  # 规则 on_timeout=allow 的超时放行
    "guardian_approved",  # Guardian 自动放行（不计入 approved 计数）
    "guardian_denied",    # Guardian 判定危险拒绝（无人可问路径）
    "guardian_bypass",    # Guardian 不可用，无人可问路径按自主性放行
})


def _audit_sink():
    """取 sqlite 审计写入面（runtime 未就绪返回 None）。"""
    try:
        from agent.runtime.singleton import get_runtime
        runtime = get_runtime()
        return runtime.data_center.router.sqlite
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
        "reason": str(reason or "")[:500],
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


def record_decision_bg(**kwargs: Any) -> None:
    """record_decision 的即发即忘入口（同步上下文用，如 resolve 内部）。

    无运行中事件循环时直接丢弃（审批决策本身已生效，仅审计缺失）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    task = spawn(record_decision(**kwargs), name="approval.audit")
    task.add_done_callback(_swallow_task_exc)


def _swallow_task_exc(task: "asyncio.Task[None]") -> None:
    """后台审计任务的异常已在 record_decision 内处理，此处仅防未消费告警。"""
    if not task.cancelled() and task.exception() is not None:
        log(f"审批审计后台任务异常: {task.exception()}", "DEBUG", tag="权限")


async def count_approvals(tool_name: str, user_id: str) -> int:
    """统计某用户对某工具的累计人工批准次数（trust 计数数据源）。"""
    sink = _audit_sink()
    if sink is None:
        return 0
    try:
        return await sink.count_approval_outcomes(tool_name, user_id, "approved")
    except Exception as exc:
        log(f"信任计数查询失败（按未达阈值处理）: {exc}", "WARNING", tag="权限")
        return 0


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
