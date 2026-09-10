"""MCPBridge 存活探测 / 禁用重连守卫单元测试。"""

from __future__ import annotations

import asyncio
from typing import Generator, List

import pytest

from core.config import ConfigManager
from entities.mcp.bridge import MCPBridge
from entities.mcp.config import MCPServerConfig


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()


@pytest.fixture()
def fast_ping_interval() -> Generator[None, None, None]:
    """缩短存活探测周期，测试后还原默认值。"""
    ConfigManager.set("mcp_liveness_ping_seconds", 0.05)
    yield
    ConfigManager.set("mcp_liveness_ping_seconds", 60)


class _FakeSession:
    """可控制 send_ping 行为的假会话。"""

    def __init__(self, ping_error: Exception | None = None) -> None:
        self.ping_error = ping_error
        self.ping_calls = 0

    async def send_ping(self) -> None:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error


def test_liveness_wait_returns_on_stop(bridge: MCPBridge, fast_ping_interval: None) -> None:
    """停止信号到达时正常返回，周期 ping 持续进行。"""
    session = _FakeSession()

    async def scenario() -> None:
        stop = asyncio.Event()
        waiter = asyncio.ensure_future(
            bridge._wait_with_liveness(session, stop)
        )
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(waiter, timeout=2)

    bridge._run_coro(scenario(), timeout=5)
    assert session.ping_calls >= 1


def test_liveness_ping_failure_raises(bridge: MCPBridge, fast_ping_interval: None) -> None:
    """ping 失败必须抛 ConnectionError，让 lifecycle 断线分支接管（重连或退出清理）。"""
    session = _FakeSession(ping_error=RuntimeError("stream closed"))

    async def scenario() -> None:
        stop = asyncio.Event()
        await bridge._wait_with_liveness(session, stop)

    with pytest.raises(ConnectionError, match="存活探测失败"):
        bridge._run_coro(scenario(), timeout=5)


def test_liveness_disabled_by_zero_interval(bridge: MCPBridge) -> None:
    """mcp_liveness_ping_seconds=0 时退化为纯停止等待，不做任何 ping。"""
    ConfigManager.set("mcp_liveness_ping_seconds", 0)
    try:
        session = _FakeSession()

        async def scenario() -> None:
            stop = asyncio.Event()
            waiter = asyncio.ensure_future(
                bridge._wait_with_liveness(session, stop)
            )
            await asyncio.sleep(0.15)
            stop.set()
            await asyncio.wait_for(waiter, timeout=2)

        bridge._run_coro(scenario(), timeout=5)
        assert session.ping_calls == 0
    finally:
        ConfigManager.set("mcp_liveness_ping_seconds", 60)


def test_try_reconnect_skips_disabled_server(bridge: MCPBridge) -> None:
    """禁用的 server 调用失败后不得被重连复活（enabled=false 不应被重新拉起）。"""
    bridge.config.servers.append(
        MCPServerConfig(name="s1", command="fake-cmd", enabled=False)
    )
    connect_calls: List[str] = []

    async def spy_connect(srv: MCPServerConfig) -> int:
        connect_calls.append(srv.name)
        return 0

    bridge._connect_server = spy_connect  # type: ignore[method-assign]

    result = bridge._run_coro(bridge._try_reconnect("s1"), timeout=5)

    assert result is False
    assert connect_calls == []
