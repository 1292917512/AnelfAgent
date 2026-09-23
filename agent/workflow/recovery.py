"""工作流启动恢复 — 崩溃残留收敛（对齐委托 recovery 的 at-most-once 范式）。

进程崩溃时运行中的工作流永远停在 running。启动扫描将其收敛为
stopped(interrupted)——不合成步骤结局、不重派执行（续跑是显式决策），
归属会话注入一条中断通知（AI 得知工作流被打断，可用 workflow_resume
续跑或修订重启）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from agent.workflow import journal as wf_journal
from core.log import log

_TAG = "工作流"


async def recover_interrupted_workflows(mind: Any) -> List[Dict[str, Any]]:
    """收敛崩溃残留的 running run；返回收敛清单（供启动日志/测试断言）。

    只收敛「上一进程遗留」：跳过已在引擎在飞表中的 run 与引擎构造之后
    创建的 run——恢复任务与 Web/工具启动并发执行，晚到的恢复不得误伤
    本进程新启动的工作流。
    """
    engine = getattr(mind, "workflow_engine", None)
    journal = getattr(engine, "journal", None)
    if journal is None:
        return []
    live_ids = set(engine.running_ids()) if engine is not None else set()
    started_at = getattr(engine, "_started_at", 0.0)
    stale = [
        run for run in await journal.list_non_terminal_runs()
        if run["id"] not in live_ids and float(run.get("created_at") or 0.0) < started_at
    ]
    for run in stale:
        await journal.settle_run(
            run["id"], wf_journal.STOPPED, stop_reason="interrupted",
            failure={"message": "进程中断（已完成步骤保留，可续跑）"},
        )
        await journal.append_event(run["id"], "run-settled", {
            "status": wf_journal.STOPPED, "stop_reason": "interrupted"})
    if stale:
        summary = "、".join(f"{r['id']}[{r.get('name', '')}]" for r in stale)
        log(f"检测到上次进程退出时在飞的工作流 {len(stale)} 个（{summary}），"
            "已标记为中断——已完成步骤保留，可续跑（workflow_resume / Web 工作流页）",
            "WARNING", tag=_TAG)
    for run in stale:
        await _notify_scope(mind, run)
    return stale


async def _notify_scope(mind: Any, run: Dict[str, Any]) -> None:
    """向归属会话注入中断通知（非会话 scope 静默跳过；失败不阻断启动）。"""
    scope = str(run.get("scope") or "")
    if not scope.startswith(("user_", "group_")):
        return
    spec_name = str(run.get("name") or run["id"])
    note = (
        f"[系统] 工作流 [{spec_name}]（{run['id']}）因进程重启被中断。\n"
        "已完成的步骤已保留（续跑不会重复执行）；可调用 workflow_resume 续跑，"
        "或调整规格后用 workflow_start 以 resume_of 修订重启。"
    )
    try:
        from agent.mind.tools.scheduler import enqueue_scope_reply
        await enqueue_scope_reply(
            mind.pfc, scope, mind.pfc.get_adapter_key(scope),
            f"工作流中断: {spec_name}", note,
        )
    except Exception as exc:
        log(f"工作流中断通知注入失败（已跳过）: {run['id']}: {exc}", "WARNING", tag=_TAG)

