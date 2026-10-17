"""执行计时独立于系统时钟，退出后不计入观测消费的耗时。"""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from core.observation import ExecutionObservation


@pytest.mark.parametrize("error", [None, RuntimeError("failed"), asyncio.CancelledError("stop")])
def test_observation_freezes_time_and_preserves_exception(monkeypatch, error):
    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr("core.observation.time", SimpleNamespace(monotonic=lambda: clock.value))
    observation = ExecutionObservation()
    clock.value += 0.125
    assert observation.elapsed_ms == 125
    expected = pytest.raises(type(error)) if error is not None else nullcontext()
    with expected as caught:
        with observation:
            clock.value += 0.25
            if error is not None:
                raise error
    if error is not None:
        assert caught.value is error
    assert observation.error is error
    assert observation.cancelled is isinstance(error, asyncio.CancelledError)
    clock.value += 100
    assert observation.elapsed_ms == 375


@pytest.mark.parametrize("error,status", [(None, "skip"), (RuntimeError("failed"), "error"),
                                          (asyncio.CancelledError("stop"), "cancelled")])
async def test_hook_observation_restores_parent_and_preserves_outcome(monkeypatch, error, status):
    from core.hooks import HookMode, current_hook_run, hook_execution, hook_journal

    events = []

    async def emit(event, payload):
        events.append(payload)

    monkeypatch.setattr("core.hooks.event_bus.emit", emit)
    previous = current_hook_run.get()
    async with hook_execution("parent", "probe", HookMode.SYNC) as parent:
        expected = pytest.raises(type(error)) if error is not None else nullcontext()
        with expected as caught:
            async with hook_execution("child", "probe", HookMode.SYNC) as child:
                child.status = "skip"
                if error is not None:
                    raise error
        if error is not None:
            assert caught.value is error
        assert current_hook_run.get() is parent
        assert child.status == status
        assert child.duration_ms >= 0
        assert child.id not in hook_journal._active
        assert events[-1]["status"] == status
    assert current_hook_run.get() is previous
    assert [(item["name"], item["status"]) for item in events] == [
        ("parent", "running"), ("child", "running"), ("child", status), ("parent", "done")]
