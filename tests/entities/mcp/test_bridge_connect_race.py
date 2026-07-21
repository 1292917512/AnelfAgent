"""MCPBridge 连接竞态 / 超时取消 / last_error 单元测试。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Any, Generator, List

import pytest

from entities.mcp.bridge import MCPBridge, MCPServerConfig


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()


def _srv(name: str = "s1") -> MCPServerConfig:
    return MCPServerConfig(name=name, command="fake-cmd", enabled=True)


def _install_fake_lifecycle(bridge: MCPBridge, started: List[str]) -> None:
    """安装可控的假 lifecycle：立即就绪，挂起直到收到停止信号。"""

    async def fake_lifecycle(
        srv: MCPServerConfig,
        stop_event: Any,
        ready_event: Any,
        result_box: List[Any],
    ) -> None:
        started.append(srv.name)
        result_box.append(1)
        ready_event.set()
        await stop_event.wait()

    bridge._server_lifecycle = fake_lifecycle  # type: ignore[method-assign]


def test_reconnect_stops_previous_lifecycle_task(bridge: MCPBridge) -> None:
    """重复连接同一 server：旧 lifecycle task 必须被停止，不得失联泄漏。"""
    started: List[str] = []
    _install_fake_lifecycle(bridge, started)

    srv = _srv()
    bridge._run_coro(bridge._connect_server(srv))
    with bridge._lock:
        first_task = bridge._lifecycle_tasks["s1"]

    bridge._run_coro(bridge._connect_server(srv))
    with bridge._lock:
        second_task = bridge._lifecycle_tasks["s1"]

    assert first_task is not second_task
    assert first_task.done()
    assert not second_task.done()
    assert started == ["s1", "s1"]


def test_failed_connect_records_last_error(bridge: MCPBridge) -> None:
    """首次连接失败应记录 last_error 且不再抛出后留驻残留 task。"""

    async def failing_lifecycle(
        srv: MCPServerConfig,
        stop_event: Any,
        ready_event: Any,
        result_box: List[Any],
    ) -> None:
        result_box.append(RuntimeError("boom"))
        ready_event.set()

    bridge._server_lifecycle = failing_lifecycle  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        bridge._run_coro(bridge._connect_server(_srv()))

    assert "boom" in bridge.get_last_errors()["s1"]
    with bridge._lock:
        assert "s1" not in bridge._lifecycle_tasks
        assert "s1" not in bridge._stop_events


def test_successful_connect_clears_last_error(bridge: MCPBridge) -> None:
    started: List[str] = []
    _install_fake_lifecycle(bridge, started)

    bridge._set_last_error("s1", "历史错误")
    bridge._run_coro(bridge._connect_server(_srv()))

    assert bridge.get_last_errors().get("s1", "") == ""


def test_run_coro_timeout_cancels_coroutine(bridge: MCPBridge) -> None:
    """_run_coro 等待超时后必须取消底层协程，避免后台半连接。"""
    cancelled: List[bool] = []

    async def sleeper() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with pytest.raises((TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        bridge._run_coro(sleeper(), timeout=0.2)

    # 取消投递到 MCP 事件循环需要一点时间
    deadline = time.time() + 2
    while not cancelled and time.time() < deadline:
        time.sleep(0.05)
    assert cancelled == [True]


def test_concurrent_connect_same_server_serializes(bridge: MCPBridge) -> None:
    """多线程并发 connect 同一 server：最终只有一个存活 lifecycle task。"""
    started: List[str] = []
    _install_fake_lifecycle(bridge, started)

    errors: List[BaseException] = []

    def connect() -> None:
        try:
            bridge.connect_server_by_name("s1")
        except BaseException as exc:  # noqa: BLE001 - 测试收集所有异常
            errors.append(exc)

    bridge.config.servers.append(_srv())
    threads = [threading.Thread(target=connect) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    with bridge._lock:
        alive = [
            task for task in bridge._lifecycle_tasks.values() if not task.done()
        ]
    assert len(alive) == 1
