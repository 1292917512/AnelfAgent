"""Home Assistant provider 测试：URL 规整 / 设备构建 / 增量事件 / 消息分发 /
全链路读取循环（_run_once 并发分发命令 result）。"""

from __future__ import annotations

import asyncio
import contextlib
import json

import aiohttp
import pytest

from core.config import ConfigManager
from entities.smart_home.models import SmartHomeCallError
from entities.smart_home.providers import get_provider
from entities.smart_home.providers import homeassistant as ha_module
from entities.smart_home.providers.homeassistant import HomeAssistantProvider


@pytest.fixture()
def provider() -> HomeAssistantProvider:
    """独立的 provider 实例（不触碰注册表单例，无网络）。"""
    return HomeAssistantProvider()


def _state(entity_id: str, state: str, name: str = "",
           attributes: dict | None = None) -> dict:
    attrs = dict(attributes or {})
    if name:
        attrs["friendly_name"] = name
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


class TestWsUrl:
    @pytest.mark.parametrize("raw,expected", [
        ("http://homeassistant.local:8123",
         "ws://homeassistant.local:8123/api/websocket"),
        ("https://ha.example.com/", "wss://ha.example.com/api/websocket"),
        ("ws://192.168.1.10:8123", "ws://192.168.1.10:8123/api/websocket"),
        ("192.168.1.10:8123", "ws://192.168.1.10:8123/api/websocket"),
        ("wss://ha.example.com/api/websocket",
         "wss://ha.example.com/api/websocket"),
    ])
    def test_normalization(self, provider: HomeAssistantProvider,
                           raw: str, expected: str) -> None:
        ConfigManager.set("smart_home_ha_url", raw)
        assert provider._ws_url() == expected


class TestConfigured:
    def test_unconfigured_by_default(self, provider: HomeAssistantProvider) -> None:
        ConfigManager.set("smart_home_ha_url", "")
        ConfigManager.set("smart_home_ha_token", "")
        assert provider.is_configured() is False

    def test_configured_when_url_and_token(self,
                                           provider: HomeAssistantProvider) -> None:
        ConfigManager.set("smart_home_ha_url", "http://ha.local:8123")
        ConfigManager.set("smart_home_ha_token", "token-abc")
        assert provider.is_configured() is True

    def test_env_ref_token_expanded(self, provider: HomeAssistantProvider,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HA_TEST_TOKEN", "secret-token")
        ConfigManager.set("smart_home_ha_token", "${HA_TEST_TOKEN}")
        assert provider._token() == "secret-token"


class TestBuildDevice:
    def test_area_resolved(self, provider: HomeAssistantProvider) -> None:
        provider._entity_area["light.desk"] = "书房"
        device = provider._build_device(
            _state("light.desk", "on", "台灯", {"brightness": 255}),
        )
        assert device.domain == "light"
        assert device.name == "台灯"
        assert device.area == "书房"
        assert device.available is True

    def test_name_falls_back_to_entity_id(self,
                                          provider: HomeAssistantProvider) -> None:
        device = provider._build_device(_state("sensor.x", "21.0"))
        assert device.name == "sensor.x"
        assert device.area == ""


class TestStateChanged:
    def test_upsert_notifies(self, provider: HomeAssistantProvider) -> None:
        seen = []
        provider.on_device_update = seen.append
        provider._apply_state_changed({
            "entity_id": "light.desk",
            "new_state": _state("light.desk", "on", "台灯"),
        })
        assert len(seen) == 1
        assert provider.snapshot()[0].entity_id == "light.desk"

    def test_remove_notifies_sync(self, provider: HomeAssistantProvider) -> None:
        provider._apply_state_changed({
            "entity_id": "light.desk",
            "new_state": _state("light.desk", "on"),
        })
        syncs = []
        provider.on_sync = lambda: syncs.append(True)
        provider._apply_state_changed({"entity_id": "light.desk",
                                       "new_state": None})
        assert provider.snapshot() == []
        assert syncs == [True]

    def test_remove_unknown_silent(self, provider: HomeAssistantProvider) -> None:
        syncs = []
        provider.on_sync = lambda: syncs.append(True)
        provider._apply_state_changed({"entity_id": "light.ghost",
                                       "new_state": None})
        assert syncs == []


class TestDispatch:
    @pytest.mark.asyncio
    async def test_result_resolves_pending(self,
                                           provider: HomeAssistantProvider) -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        provider._pending[7] = future
        provider._dispatch({"type": "result", "id": 7, "success": True,
                            "result": [{"entity_id": "light.x"}]})
        assert await future == [{"entity_id": "light.x"}]

    @pytest.mark.asyncio
    async def test_result_failure_raises(self,
                                         provider: HomeAssistantProvider) -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        provider._pending[8] = future
        provider._dispatch({"type": "result", "id": 8, "success": False,
                            "error": {"message": "service not found"}})
        with pytest.raises(SmartHomeCallError, match="service not found"):
            await future

    def test_event_routes_state_changed(self,
                                        provider: HomeAssistantProvider) -> None:
        provider._dispatch({
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {"entity_id": "switch.tv",
                         "new_state": _state("switch.tv", "off")},
            },
        })
        assert provider.snapshot()[0].state == "off"

    def test_singleton_registered(self) -> None:
        assert get_provider("ha") is not None


class TestCallService:
    @pytest.mark.asyncio
    async def test_disconnected_rejected(self,
                                         provider: HomeAssistantProvider) -> None:
        with pytest.raises(ConnectionError):
            await provider.call_service("light", "turn_on", "light.x", {})


def _text(payload: dict) -> "_FakeWsMessage":
    return _FakeWsMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload))


class _FakeWsMessage:
    """aiohttp WS 消息最小替身（type + data）。"""

    def __init__(self, type_: aiohttp.WSMsgType, data: str = "") -> None:
        self.type = type_
        self.data = data


class _FakeWs:
    """按 HA 协议应答的假 WebSocket。

    命令 result 只经异步迭代（读取循环）到达——复刻真实服务端行为：
    若 provider 在全量同步前没有启动并发读取循环，命令 future 无人
    办结，将死锁至超时。
    """

    _RESULTS: dict[str, object] = {
        "config/area_registry/list": [{"area_id": "a1", "name": "客厅"}],
        "config/device_registry/list": [{"id": "d1", "area_id": "a1"}],
        "config/entity_registry/list": [
            {"entity_id": "light.desk", "device_id": "d1"},
        ],
        "get_states": [_state("light.desk", "on", "台灯")],
        "subscribe_events": None,
    }

    def __init__(self) -> None:
        self._auth = [
            _text({"type": "auth_required"}),
            _text({"type": "auth_ok"}),
        ]
        self._inbox: asyncio.Queue[_FakeWsMessage | None] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def receive(self, timeout: float | None = None) -> _FakeWsMessage:
        return self._auth.pop(0)

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        if payload.get("type") == "auth":
            return
        await self._inbox.put(_text({
            "type": "result", "id": payload.get("id"), "success": True,
            "result": self._RESULTS.get(str(payload.get("type")), []),
        }))

    async def close(self) -> None:
        self.closed = True
        await self._inbox.put(None)

    def __aiter__(self) -> "_FakeWs":
        return self

    async def __anext__(self) -> _FakeWsMessage:
        msg = await self._inbox.get()
        if msg is None:
            raise StopAsyncIteration
        return msg


class _FakeSession:
    """aiohttp.ClientSession 替身（ws_connect 返回固定假连接）。"""

    def __init__(self, ws: _FakeWs) -> None:
        self._ws = ws
        self.closed = False

    async def ws_connect(self, url: str,
                         heartbeat: float | None = None) -> _FakeWs:
        return self._ws

    async def close(self) -> None:
        self.closed = True


class TestRunOnce:
    """全链路：鉴权 → 并发读取循环支撑全量同步 → 断线退出。"""

    @pytest.mark.asyncio
    async def test_full_sync_completes_via_read_loop(
            self, provider: HomeAssistantProvider,
            monkeypatch: pytest.MonkeyPatch) -> None:
        fake_ws = _FakeWs()
        fake_session = _FakeSession(fake_ws)
        monkeypatch.setattr(
            ha_module.aiohttp, "ClientSession", lambda *a, **k: fake_session,
        )
        ConfigManager.set("smart_home_ha_url", "http://ha.test:8123")
        ConfigManager.set("smart_home_ha_token", "test-fixture-mock-key-001")
        syncs: list[bool] = []
        provider.on_sync = lambda: syncs.append(True)

        task = asyncio.create_task(provider._run_once())
        try:
            # 无并发读取循环时首个命令 result 永不被分发（死锁至 15s 超时），
            # 2s 窗口足以判定读取循环在位
            for _ in range(40):
                await asyncio.sleep(0.05)
                if provider.is_connected():
                    break
            assert provider.is_connected() is True
            assert syncs == [True]
            assert provider._areas == {"a1": "客厅"}
            assert provider._entity_area["light.desk"] == "客厅"
            devices = provider.snapshot()
            assert [d.entity_id for d in devices] == ["light.desk"]
            assert devices[0].name == "台灯"
            assert any(p.get("type") == "subscribe_events" for p in fake_ws.sent)
            # 读取循环退出（连接关闭）→ _run_once 以 ConnectionError 收场
            await fake_ws.close()
            with pytest.raises(ConnectionError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await provider._teardown()


@pytest.mark.asyncio
async def test_snapshot_replays_newer_events(provider, monkeypatch):
    commands = []
    async def command(payload):
        commands.append(payload["type"])
        if payload["type"] == "get_states":
            provider._apply_state_changed({"entity_id": "light.desk", "new_state": _state("light.desk", "off")})
            provider._apply_state_changed({"entity_id": "light.removed", "new_state": None})
            return [_state("light.desk", "on"), _state("light.removed", "on")]
        return []
    monkeypatch.setattr(provider, "_command", command)
    await provider._full_sync()
    assert commands[0] == "subscribe_events"
    assert [(d.entity_id, d.state) for d in provider.snapshot()] == [("light.desk", "off")]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain,action,service", [
    ("light", "set_brightness", "turn_on"), ("media_player", "set_volume", "volume_set"),
    ("cover", "set_position", "set_cover_position"), ("scene", "activate", "turn_on"),
])
async def test_canonical_actions_translate_at_transport(provider, monkeypatch, domain, action, service):
    from unittest.mock import AsyncMock
    command = AsyncMock()
    monkeypatch.setattr(provider, "_command", command)
    provider._connected = True
    await provider.call_service(domain, action, domain + ".device", {})
    assert command.await_args.args[0]["service"] == service


def test_capabilities_hide_unsupported_controls(provider):
    from entities.smart_home.models import DeviceState
    device = DeviceState("light.binary", "light", "Simple bulb", "on", {"supported_color_modes": ["onoff"]})
    assert "set_brightness" not in provider.actions_for(device)
    player = DeviceState("media_player.basic", "media_player", "Player", "idle", {"supported_features": 4})
    assert set(provider.actions_for(player)) == {"set_volume"}
