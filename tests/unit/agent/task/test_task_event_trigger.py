"""任务事件触发（agent.task.event_trigger）单元测试：reconcile 装配与触发执行。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

from agent.hooks_llm import HookRegistry
from agent.task.event_trigger import (
    _OWNER,
    sync_task_event_hooks,
    task_event_hook_names,
)
from agent.task.model import TaskDefinition


@pytest.fixture(autouse=True)
def _clean_registry():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


def _engine(tasks: List[TaskDefinition], run_results: Optional[Dict[str, str]] = None):
    """构造一个带 task_registry 与 run_task 的轻量引擎替身。"""
    registry = SimpleNamespace(list_all=lambda: tasks)
    ran: List[str] = []

    async def _run_task(name: str) -> Optional[str]:
        ran.append(name)
        return (run_results or {}).get(name)

    engine = SimpleNamespace(task_registry=registry, run_task=_run_task)
    engine._ran = ran  # type: ignore[attr-defined]
    return engine


def _task(name: str, event: str = "", enabled: bool = True) -> TaskDefinition:
    return TaskDefinition(
        name=name, prompt="p", enabled=enabled, trigger_event=event,
    )


def test_sync_registers_hooks_for_event_tasks():
    engine = _engine([
        _task("a", "after_reply"),
        _task("b", "delegation_resolved"),
        _task("c", ""),              # 无事件触发，不装配
        _task("d", "after_reply", enabled=False),  # 禁用，不装配
    ])
    count = sync_task_event_hooks(engine)
    assert count == 2
    names = set(task_event_hook_names())
    assert names == {"task_event:a", "task_event:b"}
    assert all(HookRegistry.get(n).owner == _OWNER for n in names)


def test_sync_skips_invalid_event():
    engine = _engine([_task("bad", "bogus_event")])
    count = sync_task_event_hooks(engine)
    assert count == 0
    assert task_event_hook_names() == []


def test_sync_reconcile_removes_stale_hooks():
    engine = _engine([_task("a", "after_reply")])
    sync_task_event_hooks(engine)
    assert task_event_hook_names() == ["task_event:a"]
    # 任务删除/改配后 reconcile：旧钩子被清理
    engine2 = _engine([])
    sync_task_event_hooks(engine2)
    assert task_event_hook_names() == []


def test_sync_reconcile_updates_event_change():
    engine = _engine([_task("a", "after_reply")])
    sync_task_event_hooks(engine)
    # 同一任务改 trigger_event
    engine2 = _engine([_task("a", "context_pressure")])
    sync_task_event_hooks(engine2)
    assert task_event_hook_names() == ["task_event:a"]
    assert HookRegistry.get("task_event:a").event == "context_pressure"


async def test_task_event_hook_invokes_run_task():
    engine = _engine([_task("a", "after_reply")], run_results={"a": "done"})
    sync_task_event_hooks(engine)
    spec = HookRegistry.get("task_event:a")
    from agent.hooks_llm import HookContext
    ctx = HookContext(name=spec.name, event="after_reply", scope="", payload={})
    result = await spec.handler(ctx)
    assert engine._ran == ["a"]  # type: ignore[attr-defined]
    # 任务有产出但返回空串（不走钩子面的结果路由——任务有自己的 save_result_to_memory）
    assert result == ""


async def test_task_event_hook_none_output_returns_none():
    engine = _engine([_task("a", "after_reply")], run_results={})
    sync_task_event_hooks(engine)
    spec = HookRegistry.get("task_event:a")
    from agent.hooks_llm import HookContext
    ctx = HookContext(name=spec.name, event="after_reply", scope="", payload={})
    result = await spec.handler(ctx)
    assert engine._ran == ["a"]  # type: ignore[attr-defined]
    assert result is None
