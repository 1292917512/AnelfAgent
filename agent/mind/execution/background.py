"""后台任务等待与完成事实的轮内接续。"""


from __future__ import annotations

import time
from typing import TYPE_CHECKING, List

from agent.mind.execution.messages import _fetch_new_messages
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.background_tasks import (
        BackgroundTaskInfo,
        BackgroundTaskRegistry,
        TaskCompletion,
    )
    from agent.mind.mind import Mind


def _format_running_tasks(tasks: List["BackgroundTaskInfo"]) -> str:
    """运行中任务的一行式摘要（注入提示词用）。"""
    return "、".join(f"{t.description[:30]}({t.task_id})" for t in tasks) or "无"


def _format_task_completions(
        completions: List["TaskCompletion"],
        running: List["BackgroundTaskInfo"],
) -> str:
    """后台任务完成事件注入文本（system 角色，保持消息交替规范）。"""
    lines = ["[后台任务完成] 你等待的后台任务已结束："]
    for c in completions:
        status = "成功" if c.success else "失败"
        lines.append(f"- {c.description[:60]} ({c.task_id})：{status}")
        if c.summary:
            lines.append(f"  结果：{c.summary[:800]}")
    if running:
        lines.append(f"仍有 {len(running)} 个任务运行中：{_format_running_tasks(running)}")
    lines.append(
        "请根据结果继续处理："
        "任务用工具推进；需要告知用户的结果用 send_message 发送并核对目标与成功回执，不以普通正文代替；"
        "无需回复或无需继续操作时 end_reply。"
    )
    return "\n".join(lines)


async def _suspend_for_background(
        mind: "Mind",
        anything: "Everything",
        registry: "BackgroundTaskRegistry",
        scope: str,
        since_ts: int,
        timeout: float,
        interrupts,
        since_id: int = 0,
) -> tuple[str, List["TaskCompletion"], float]:
    """挂起等待后台任务完成，返回 (reason, completions, elapsed)。

    挂起期间新消息照常实时入库（accept_feel 不经思考循环）；
    should_abort 轮询中断信号与新消息水位，被打断时立即返回，
    由循环顶部统一并入新消息 / 处理中断，时序不受挂起影响。
    """
    log(f"检测到等待意图，挂起等待后台任务 (scope={scope}, 上限 {timeout:.0f}s)", tag="思维")
    initial_revision = mind.pfc.message_revision

    async def _aborted() -> bool:
        if interrupts is not None and interrupts.is_requested(scope):
            return True
        if mind.pfc.message_revision != initial_revision:
            return True
        return bool(await _fetch_new_messages(mind, anything, since_ts, since_id))

    t0 = time.monotonic()
    result = await registry.wait_any(scope, timeout=timeout, should_abort=_aborted)
    return result.reason, result.completions, time.monotonic() - t0
