"""任务事件触发：把带 trigger_event 的任务经 LLM 钩子面装配为事件钩子。

任务的事件触发是与四种时间调度（heartbeat/scheduled/idle/manual）正交的
第五种触发方式——时间调度管"何时跑"，事件触发管"发生了什么之后跑"。

装配模型：
- 扫描任务注册表中 trigger_event 非空的任务，每个任务注册一个
  ``task_event:<task_name>`` 钩子（context=none——任务用 lean 精简上下文，
  不继承触发会话的 transcript，避免跨会话上下文串扰与成本）；
- 命中后调 ``HeartbeatEngine.run_task(name)``——复用引擎既有的
  ``_task_inflight`` 同任务去重、手动/调度并发互斥与执行历史落盘；
- 事件触发不进 heartbeat.json 调度（与时间调度正交），不持久化计数。

注册归属 owner=task.events：reload 时先按 owner 整体注销再重建，保证
任务 CRUD（改名/删除/改 trigger_event）即时生效、无孤儿钩子。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from agent.hooks_llm import HOOK_EVENTS, HookContext, HookRegistry, llm_hook
from core.log import log

if TYPE_CHECKING:
    from agent.heartbeat.engine import HeartbeatEngine

_OWNER = "task.events"
_HOOK_NAME_PREFIX = "task_event:"


def sync_task_event_hooks(engine: "HeartbeatEngine") -> int:
    """ reconcile 任务事件钩子：按当前任务注册表重建全部事件钩子，返回装配数。

    先按 owner 整体注销旧钩子再扫描重建（幂等）——任务 CRUD 经 reload
    触发热更，改名/删除/改 trigger_event 即时生效、无残留。引擎构造
    （首次）与 reload（热更）共用本函数。
    """
    HookRegistry.unregister_by_owner(_OWNER)
    count = 0
    for task in engine.task_registry.list_all():
        event = (task.trigger_event or "").strip()
        if not event or not task.enabled:
            continue
        if event not in HOOK_EVENTS:
            log(
                f"任务 [{task.name}] trigger_event={event!r} 非法"
                f"（须为 {sorted(HOOK_EVENTS)} 之一），跳过装配",
                "WARNING", tag="任务",
            )
            continue
        _register_one(engine, task.name, event)
        count += 1
    if count:
        log(f"任务事件钩子已装配: {count} 个", "DEBUG", tag="任务")
    return count


def _register_one(engine: "HeartbeatEngine", task_name: str, event: str) -> None:
    """为单个任务注册事件钩子（命中后经引擎 run_task 执行）。"""

    @llm_hook(
        f"{_HOOK_NAME_PREFIX}{task_name}", event=event, context="none",
        allow_output_tools=False, max_concurrent=1,
        owner=_OWNER, source="task",
        description=f"任务 [{task_name}] 的事件触发（{event}）",
    )
    async def _task_event_hook(ctx: HookContext) -> Optional[str]:
        log(f"任务 [{task_name}] 事件触发（{event}）", "DEBUG", tag="任务")
        # run_task 自带 _task_inflight 去重与并发互斥；产出经执行历史落盘，
        # 不由钩子面登记（任务有自己的结果路由：save_result_to_memory）
        result = await engine.run_task(task_name)
        return None if result is None else ""  # 空串 = 有产出但不走钩子路由


def task_event_hook_names() -> List[str]:
    """当前已装配的任务事件钩子名清单（观测/测试用）。"""
    return [s.name for s in HookRegistry.list_all() if s.owner == _OWNER]
