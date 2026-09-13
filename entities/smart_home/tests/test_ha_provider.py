"""Home Assistant provider 测试：URL 规整 / 设备构建 / 增量事件 / 消息分发。"""

from __future__ import annotations

import asyncio

import pytest

from core.config import ConfigManager
from entities.smart_home.providers import get_provider
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
        with pytest.raises(RuntimeError, match="service not found"):
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
