"""AgentAssistant 异常恢复：周期异常后 PFC 仍有待处理项时主动调度下一轮。

回归背景：_run_loop 对 execute_mind 异常只记日志不重试，长任务执行期间
消息触发的周期一旦异常，用户消息要等任务收尾或下次心跳才被处理。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

from agent.runtime.assistant import AgentAssistant


def _fake_mind(*, has_pending: bool, scheduled: List[str]) -> Any:
    return SimpleNamespace(
        is_reflecting=False,
        execute_mind=AsyncMock(side_effect=RuntimeError("boom")),
        pfc=SimpleNamespace(has_pending_tasks=lambda: has_pending),
        _schedule_next_cycle=lambda reason: scheduled.append(reason),
    )


async def _run_one_batch(assistant: AgentAssistant, scheduled: List[str]) -> None:
    await assistant._queue.put(object())
    task = asyncio.create_task(assistant._run_loop())
    try:
        # 等待首批消息处理完毕（队列 task_done 归零）或调度已触发
        for _ in range(50):
            await asyncio.sleep(0)
            if scheduled:
                break
        await asyncio.wait_for(assistant._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestRunLoopExceptionRecovery:
    async def test_reschedules_when_pending_tasks_remain(self) -> None:
        scheduled: List[str] = []
        assistant = AgentAssistant(_fake_mind(has_pending=True, scheduled=scheduled), heartbeat_enabled=False)
        await _run_one_batch(assistant, scheduled)
        assert len(scheduled) == 1

    async def test_no_reschedule_when_nothing_pending(self) -> None:
        scheduled: List[str] = []
        assistant = AgentAssistant(_fake_mind(has_pending=False, scheduled=scheduled), heartbeat_enabled=False)
        await _run_one_batch(assistant, scheduled)
        assert scheduled == []
