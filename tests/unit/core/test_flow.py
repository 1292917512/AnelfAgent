"""FlowMachine 流程状态机单元测试。"""
from __future__ import annotations

import asyncio

import pytest

from core.flow import FlowCycleError, FlowMachine, NodeState


async def _ok(value: object = None) -> object:
    return value


async def test_sequential_chain_default_preserves_registration_order():
    """未声明 depends_on 的节点弱链式依赖前驱，按注册顺序串行。"""
    machine = FlowMachine()
    executed: list[str] = []

    @machine.node()
    async def first() -> None:
        executed.append("first")

    @machine.node()
    async def second() -> None:
        executed.append("second")

    result = await machine.execute()
    assert result.success
    assert executed == ["first", "second"]


async def test_independent_nodes_run_concurrently():
    """depends_on=[] 的同层节点并发执行。"""
    machine = FlowMachine()
    started: list[str] = []
    done = asyncio.Event()

    async def make(name: str) -> None:
        started.append(name)
        if len(started) < 2:
            # 等另一个节点启动：若串行执行将超时
            await asyncio.wait_for(done.wait(), timeout=1.0)

    @machine.node(depends_on=[])
    async def node_a() -> None:
        await make("a")
        done.set()

    @machine.node(depends_on=[])
    async def node_b() -> None:
        await make("b")
        done.set()

    result = await machine.execute()
    assert result.success
    assert sorted(started) == ["a", "b"]


async def test_depends_on_orders_execution():
    machine = FlowMachine()
    executed: list[str] = []

    @machine.node(depends_on=["setup"])
    async def teardown() -> None:
        executed.append("teardown")

    @machine.node(depends_on=[])
    async def setup() -> None:
        executed.append("setup")

    result = await machine.execute()
    assert result.success
    assert executed == ["setup", "teardown"]


async def test_cycle_rejected_before_execution():
    machine = FlowMachine()

    @machine.node(depends_on=["b"])
    async def a() -> None:
        pass

    @machine.node(depends_on=["a"])
    async def b() -> None:
        pass

    with pytest.raises(FlowCycleError):
        await machine.execute()


async def test_unknown_dependency_rejected():
    machine = FlowMachine()

    @machine.node(depends_on=["ghost"])
    async def a() -> None:
        pass

    with pytest.raises(FlowCycleError):
        await machine.execute()


async def test_duplicate_node_name_rejected():
    machine = FlowMachine()

    @machine.node()
    async def dup() -> None:
        pass

    @machine.node()
    async def dup() -> None:  # noqa: F811
        pass

    with pytest.raises(FlowCycleError):
        await machine.execute()


async def test_strong_dependency_marks_upstream_failed():
    """显式强依赖：上游失败（被容忍）时下游标记 UPSTREAM_FAILED 且不执行。"""
    machine = FlowMachine()
    executed: list[str] = []

    @machine.node(skip_on_error=True, depends_on=[])
    async def flaky() -> None:
        raise RuntimeError("boom")

    @machine.node(depends_on=["flaky"])
    async def downstream() -> None:
        executed.append("downstream")

    result = await machine.execute()
    assert result.success  # flaky 被容忍，流程整体仍成功
    assert executed == []
    states = {r.name: r.state for r in result.results}
    assert states["flaky"] is NodeState.SKIPPED
    assert states["downstream"] is NodeState.UPSTREAM_FAILED


async def test_weak_chain_does_not_block_on_predecessor_failure():
    """弱链式依赖保持旧顺序语义：前驱被容忍的失败不阻断后继。"""
    machine = FlowMachine()
    executed: list[str] = []

    @machine.node(skip_on_error=True)
    async def flaky() -> None:
        raise RuntimeError("boom")

    @machine.node()
    async def next_node() -> None:
        executed.append("next")

    result = await machine.execute()
    assert result.success
    assert executed == ["next"]


async def test_hard_failure_aborts_flow():
    machine = FlowMachine()
    executed: list[str] = []

    @machine.node(depends_on=[])
    async def bad() -> None:
        raise RuntimeError("boom")

    @machine.node(depends_on=["bad"])
    async def after() -> None:
        executed.append("after")

    result = await machine.execute()
    assert not result.success
    assert executed == []
    assert any(r.state is NodeState.FAILED for r in result.results)


async def test_retries_with_delay_table():
    """重试按查表退避，超出次数后落 FAILED；成功则记录尝试次数。"""
    machine = FlowMachine()
    calls = 0

    @machine.node(retries=2, retry_delay=[0.01, 0.02], depends_on=[])
    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(f"fail {calls}")

    result = await machine.execute()
    assert result.success
    assert calls == 3
    node_result = result.results[0]
    assert node_result.state is NodeState.SUCCESS
    assert node_result.attempts == 3


async def test_retries_exhausted_falls_to_failed():
    machine = FlowMachine()
    calls = 0

    @machine.node(retries=1, retry_delay=0.0, depends_on=[])
    async def always_bad() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    result = await machine.execute()
    assert not result.success
    assert calls == 2
    assert result.results[0].attempts == 2


async def test_timeout_converts_to_timeout_error():
    machine = FlowMachine()

    @machine.node(timeout=0.05, depends_on=[])
    async def slow() -> None:
        await asyncio.sleep(5)

    result = await machine.execute()
    assert not result.success
    assert isinstance(result.results[0].error, TimeoutError)


async def test_crash_passthrough_records_state():
    """BaseException 记录 CRASHED 后穿透，skip_on_error 不可吞。"""
    machine = FlowMachine()

    @machine.node(skip_on_error=True, depends_on=[])
    async def crashing() -> None:
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        await machine.execute()


async def test_blackboard_result_propagation():
    machine = FlowMachine()

    @machine.node(depends_on=[])
    async def producer() -> str:
        return "hello"

    @machine.node(depends_on=["producer"])
    async def consumer() -> None:
        assert machine.get_result("producer") == "hello"

    result = await machine.execute()
    assert result.success


async def test_counts_message_summary():
    machine = FlowMachine()

    @machine.node(depends_on=[])
    async def good() -> None:
        pass

    @machine.node(skip_on_error=True, depends_on=[])
    async def bad() -> None:
        raise RuntimeError("boom")

    result = await machine.execute()
    message = result.counts_message()
    assert "1 success" in message
    assert "1 skipped" in message
