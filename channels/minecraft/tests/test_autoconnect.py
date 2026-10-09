"""自动进服：防抖、冷却、世界选择与连接参数回归。"""

from collections.abc import Iterator

import pytest

from channels.minecraft.autoconnect import (
    _CONNECT_STATE,
    _DISCONNECT_DEBOUNCE_SECONDS,
    _RETRY_COOLDOWN_SECONDS,
    AutoConnector,
)
from channels.minecraft.discovery import LanWorld


class FakeClock:
    """可拨动的单调时钟。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ConnectRecorder:
    """记录 connect_bot 调用，可切换失败模式。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    async def __call__(self, tool: str, args: dict) -> dict:
        assert tool == "connect_bot"
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(f"{args['host']}:{args['port']}:{args['username']}:{args['auth']}")
        return {"success": True}


@pytest.fixture
def env() -> Iterator[tuple[AutoConnector, FakeClock, ConnectRecorder, list[LanWorld], list[str]]]:
    clock = FakeClock()
    connect = ConnectRecorder()
    worlds: list[LanWorld] = []
    announced: list[str] = []

    async def discover() -> list[LanWorld]:
        return list(worlds)

    connector = AutoConnector(connect, discover, announced.append, bot_username="AnelfBot", clock=clock)
    yield connector, clock, connect, worlds, announced


async def test_connected_state_is_noop(env) -> None:
    connector, clock, connect, _, _ = env
    for _ in range(5):
        await connector.tick(_CONNECT_STATE)
        clock.advance(1.0)
    await connector.wait_idle()
    assert connect.calls == []


async def test_transient_disconnect_does_not_trigger(env) -> None:
    """断线短于防抖窗口不动手（AI 可能正在自己重连）。"""
    connector, clock, connect, _, _ = env
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS - 1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == []


async def test_debounced_disconnect_joins_most_recent_world(env) -> None:
    """防抖期满后进服：多世界选最近广播的端口，host 强制回环（MCP 白名单只放行回环）。"""
    connector, clock, connect, worlds, announced = env
    worlds.extend(
        [
            LanWorld(host="192.168.1.10", port=1234, last_seen=1.0),
            LanWorld(host="192.168.1.10", port=5678, last_seen=9.0),
        ]
    )
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == ["127.0.0.1:5678:AnelfBot:offline"]
    assert announced == ["我来了！"]


async def test_online_state_is_noop(env) -> None:
    """执行器 status=online（真实取值）视为已连接，永不触发进服。"""
    connector, clock, connect, worlds, _ = env
    worlds.append(LanWorld(host="192.168.31.123", port=9905))
    for _ in range(5):
        await connector.tick("online")
        clock.advance(_RETRY_COOLDOWN_SECONDS + 1)
    await connector.wait_idle()
    assert connect.calls == []


async def test_already_connected_treated_as_joined(env) -> None:
    """AI 抢先手动连上时 connect_bot 抛 ALREADY_CONNECTED：静默复位，不报失败不进冷却。"""
    connector, clock, connect, worlds, announced = env
    worlds.append(LanWorld(host="192.168.31.123", port=9905))
    connect.fail_with = RuntimeError(
        "MCP 工具 'connect_bot' 执行失败: Error [ALREADY_CONNECTED]: "
        "A bot (AnelfBot) is already connected."
    )
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    # 不播报（不是本模块连上的）、不冷却（下次断线可立即再试）
    assert announced == []
    connect.fail_with = None
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == ["127.0.0.1:9905:AnelfBot:offline"]


async def test_username_resolved_at_join_time() -> None:
    """bot_username 支持 callable：配置热更后按新值进服。"""
    clock = FakeClock()
    connect = ConnectRecorder()
    username = {"value": "OldBot"}

    async def discover() -> list[LanWorld]:
        return [LanWorld(host="127.0.0.1", port=25565)]

    connector = AutoConnector(connect, discover, bot_username=lambda: username["value"], clock=clock)
    username["value"] = "NewBot"
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == ["127.0.0.1:25565:NewBot:offline"]


async def test_empty_discovery_goes_to_cooldown(env) -> None:
    """没发现世界：不连接，且冷却期内不再尝试。"""
    connector, clock, connect, _, _ = env
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == []
    clock.advance(_RETRY_COOLDOWN_SECONDS - 1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == []


async def test_connect_failure_backs_off(env) -> None:
    connector, clock, connect, worlds, _ = env
    worlds.append(LanWorld(host="127.0.0.1", port=9999))
    connect.fail_with = RuntimeError("ECONNREFUSED")
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert len(connect.calls) == 0  # 失败不计入成功调用
    clock.advance(_RETRY_COOLDOWN_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert len(connect.calls) == 0


async def test_reconnect_after_failure_recovers(env) -> None:
    """失败冷却后世界恢复，重连成功并播报。"""
    connector, clock, connect, worlds, announced = env
    worlds.append(LanWorld(host="127.0.0.1", port=9999))
    connect.fail_with = RuntimeError("ECONNREFUSED")
    await connector.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    connect.fail_with = None
    clock.advance(_RETRY_COOLDOWN_SECONDS + 0.1)
    await connector.tick("disconnected")
    await connector.wait_idle()
    assert connect.calls == ["127.0.0.1:9999:AnelfBot:offline"]
    assert announced == ["我来了！"]


async def test_reconnection_does_not_restart_while_task_running(env) -> None:
    """发现/连接进行中再来 tick 不得并发第二个任务。"""
    import asyncio

    connector, clock, connect, worlds, _ = env
    worlds.append(LanWorld(host="127.0.0.1", port=25565))
    started = asyncio.Event()
    release = asyncio.Event()

    async def gated_connect(tool: str, args: dict) -> dict:
        started.set()
        await release.wait()
        return {"success": True}

    slow = AutoConnector(
        gated_connect,
        lambda: asyncio.sleep(0, result=list(worlds)),
        bot_username="AnelfBot",
        clock=clock,
    )
    await slow.tick("disconnected")
    clock.advance(_DISCONNECT_DEBOUNCE_SECONDS + 0.1)
    await slow.tick("disconnected")
    await started.wait()
    await slow.tick("disconnected")
    await slow.tick("disconnected")
    release.set()
    await slow.wait_idle()
    assert connect.calls == []
