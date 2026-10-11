"""供应商组件测试：多供应商路由 / 小米组件 / 小度 MCP 传输组件。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from core.tool_errors import ErrorCause
from entities.smart_home.manager import SmartHomeManager
from entities.smart_home.models import DeviceState, SmartHomeCallError
from entities.smart_home.providers.base import SmartHomeProvider


def _device(entity_id: str, domain: str = "media_player",
            name: str = "设备", state: str = "idle") -> DeviceState:
    return DeviceState(entity_id=entity_id, domain=domain, name=name,
                       state=state)


class FakeProvider(SmartHomeProvider):
    """内存假平台（可配置前缀/连接态/播报支持）。"""

    key = "fake"

    def __init__(self, devices: List[DeviceState], prefix: str = "",
                 speak: bool = False, connected: bool = True) -> None:
        super().__init__()
        self.entity_prefix = prefix
        self._devices = devices
        self._connected = connected
        self._speak = speak
        self.calls: List[Dict[str, Any]] = []
        self.spoken: List[str] = []

    def is_configured(self) -> bool:
        return True

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> List[DeviceState]:
        return list(self._devices)

    async def call_service(self, domain: str, service: str, entity_id: str,
                           data: Dict[str, Any]) -> None:
        self.calls.append({"domain": domain, "service": service,
                           "entity_id": entity_id, "data": data})

    def status(self) -> Dict[str, Any]:
        return {"provider": self.key, "provider_name": self.key,
                "configured": True, "connected": self._connected,
                "device_count": len(self._devices), "last_error": None,
                "connected_at": None}

    def supports_speak(self, device: DeviceState) -> bool:
        return self._speak

    async def speak(self, device: DeviceState, text: str) -> None:
        self.spoken.append(f"{device.entity_id}:{text}")


def _manager(monkeypatch: pytest.MonkeyPatch,
             providers: List[FakeProvider]) -> SmartHomeManager:
    monkeypatch.setattr(
        "entities.smart_home.manager.all_providers", lambda: providers,
    )
    return SmartHomeManager()


class TestMultiProvider:
    @pytest.mark.asyncio
    async def test_owner_routing_by_prefix(self,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        xiaomi = FakeProvider([_device("xiaomi.s1")], prefix="xiaomi.")
        ha = FakeProvider([_device("light.desk", "light")])
        mgr = _manager(monkeypatch, [xiaomi, ha])
        assert mgr.owner_of("xiaomi.s1") is xiaomi
        assert mgr.owner_of("light.desk") is ha

    @pytest.mark.asyncio
    async def test_devices_aggregated(self,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        a = FakeProvider([_device("xiaomi.s1")], prefix="xiaomi.")
        b = FakeProvider([_device("light.desk", "light")])
        mgr = _manager(monkeypatch, [a, b])
        assert {d.entity_id for d in mgr.devices()} == {"xiaomi.s1", "light.desk"}

    @pytest.mark.asyncio
    async def test_call_routed_to_owner(self,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        xiaomi = FakeProvider([_device("xiaomi.s1")], prefix="xiaomi.")
        ha = FakeProvider([_device("light.desk", "light", state="on")])
        mgr = _manager(monkeypatch, [xiaomi, ha])
        await mgr.call_service("xiaomi.s1", "media_play")
        assert xiaomi.calls[0]["entity_id"] == "xiaomi.s1"
        assert ha.calls == []

    @pytest.mark.asyncio
    async def test_speak_native_preferred(self,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeProvider([_device("xiaomi.s1")], prefix="xiaomi.",
                                speak=True)
        mgr = _manager(monkeypatch, [provider])
        result = await mgr.call_service("xiaomi.s1", "speak", "你好")
        assert provider.spoken == ["xiaomi.s1:你好"]
        assert provider.calls == []
        assert result["service"] == "fake.speak"

    @pytest.mark.asyncio
    async def test_speak_falls_back_to_domain(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.config import ConfigManager
        ConfigManager.set("smart_home_media_player_tts_service", "tts.cloud_say")
        provider = FakeProvider([_device("media_player.tv")], speak=False)
        mgr = _manager(monkeypatch, [provider])
        await mgr.call_service("media_player.tv", "speak", "你好")
        assert provider.calls[0]["domain"] == "tts"
        assert provider.calls[0]["data"] == {"message": "你好"}
        ConfigManager.set("smart_home_media_player_tts_service", "")

    @pytest.mark.asyncio
    async def test_speak_requires_text(self,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeProvider([_device("xiaomi.s1")], prefix="xiaomi.",
                                speak=True)
        mgr = _manager(monkeypatch, [provider])
        with pytest.raises(SmartHomeCallError) as info:
            await mgr.call_service("xiaomi.s1", "speak", "  ")
        assert info.value.cause == ErrorCause.PARAM


class TestXiaomiProvider:
    @pytest.fixture()
    def provider(self):
        from entities.smart_home.providers.xiaomi import XiaomiProvider
        return XiaomiProvider()

    @pytest.mark.asyncio
    async def test_sync_devices_maps_speakers(self, provider) -> None:
        class FakeClient:
            async def speakers(self) -> List[Dict[str, Any]]:
                return [
                    {"deviceID": "abc123", "name": "客厅小爱"},
                    {"deviceID": "def456", "alias": "卧室小爱"},
                    {"name": "无ID忽略"},
                ]

        provider._client = FakeClient()
        await provider._sync_devices()
        devices = {d.entity_id: d for d in provider.snapshot()}
        assert set(devices) == {"xiaomi.abc123", "xiaomi.def456"}
        assert devices["xiaomi.abc123"].name == "客厅小爱"
        assert devices["xiaomi.abc123"].domain == "media_player"

    @pytest.mark.asyncio
    async def test_call_service_mapping(self, provider) -> None:
        calls: List[tuple] = []

        class FakeClient:
            async def set_volume(self, device_id: str, volume: int) -> None:
                calls.append(("volume", device_id, volume))

            async def play(self, device_id: str) -> None:
                calls.append(("play", device_id))

        provider._client = FakeClient()
        provider._connected = True
        provider._device_ids["xiaomi.abc"] = "abc"
        await provider.call_service("media_player", "volume_set", "xiaomi.abc",
                                    {"volume_level": 0.4})
        await provider.call_service("media_player", "media_play", "xiaomi.abc", {})
        assert calls == [("volume", "abc", 40), ("play", "abc")]

    @pytest.mark.asyncio
    async def test_speak_native(self, provider) -> None:
        spoken: List[str] = []

        class FakeClient:
            async def speak(self, device_id: str, text: str) -> None:
                spoken.append(f"{device_id}:{text}")

        provider._client = FakeClient()
        provider._connected = True
        provider._device_ids["xiaomi.abc"] = "abc"
        device = _device("xiaomi.abc")
        assert provider.supports_speak(device) is True
        await provider.speak(device, "播报测试")
        assert spoken == ["abc:播报测试"]

    @pytest.mark.asyncio
    async def test_connect_failure_recorded(self, provider,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
        from entities.smart_home.providers import xiaomi as xiaomi_mod

        class BrokenClient:
            async def open(self, u: str, p: str) -> None:
                raise RuntimeError("登录失败: 账号或密码错误")

            async def close(self) -> None:
                pass

        monkeypatch.setattr(xiaomi_mod, "XiaomiClient", BrokenClient)
        from entities.smart_home.providers.xiaomi import XiaomiProvider
        broken = XiaomiProvider()
        await broken.connect()
        assert broken.is_connected() is False
        assert "账号或密码错误" in (broken.status()["last_error"] or "")


class TestXiaoduMcpProvider:
    """小度 MCP 传输型 provider：设备映射 / 动作翻译 / 原生播报。"""

    @pytest.fixture()
    def env(self, monkeypatch: pytest.MonkeyPatch):
        from entities.smart_home.providers import xiaodu as xiaodu_mod

        calls: List[tuple] = []
        responses = {
            ("xiaodu-iot", "GET_ALL_DEVICES_WITH_STATUS"): {"return": [
                {"applianceName": "云佳", "roomName": "主卧",
                 "applianceTypes": ["AIR_CONDITION"],
                 "status": {"turnOnState": "OFF", "connectivity": "REACHABLE",
                            "targetTemperature": 25}},
                {"applianceName": "台灯", "roomName": "书房",
                 "applianceTypes": ["LIGHT"],
                 "status": {"turnOnState": "ON", "connectivity": "REACHABLE"}},
                {"applianceName": "窗帘", "applianceTypes": ["CURTAIN"],
                 "status": {}},
                {"applianceName": "加湿器", "applianceTypes": ["LIGHT"],
                 "status": {"turnOnState": "ON", "connectivity": "UNREACHABLE"}},
            ]},
            ("xiaodu", "list_user_devices"): [
                {"cuid": "c1", "client_id": "k1", "device_name": "小度音箱",
                 "online_status": True, "location": {"room": "客厅"}},
            ],
        }

        async def fake_call(server: str, tool: str, arguments: Dict[str, Any]):
            calls.append((server, tool, arguments))
            return json.dumps(responses.get((server, tool), {}))

        monkeypatch.setattr(xiaodu_mod, "call_mcp_server_tool", fake_call)
        monkeypatch.setattr(xiaodu_mod, "bridge_server_status", lambda: [
            {"name": "xiaodu-iot", "enabled": True, "connected": True},
            {"name": "xiaodu", "enabled": True, "connected": True},
        ])
        return xiaodu_mod.XiaoduProvider(), calls

    @pytest.mark.asyncio
    async def test_sync_maps_devices(self, env) -> None:
        provider, _ = env
        assert provider.is_configured() is True
        assert await provider._sync_devices() is True
        devices = {d.entity_id: d for d in provider.snapshot()}
        # 无域组件的类型（CURTAIN）不进设备面；不可达 → unavailable
        assert set(devices) == {
            "xiaodu.云佳", "xiaodu.台灯", "xiaodu.加湿器", "xiaodu.c1",
        }
        ac = devices["xiaodu.云佳"]
        assert (ac.domain, ac.state, ac.area) == ("climate", "off", "主卧")
        assert ac.attributes["temperature"] == 25
        assert devices["xiaodu.加湿器"].available is False
        spk = devices["xiaodu.c1"]
        assert (spk.domain, spk.state, spk.area) == ("media_player", "idle", "客厅")
        assert provider.supports_speak(spk) is True

    @pytest.mark.asyncio
    async def test_iot_action_translation(self, env) -> None:
        provider, calls = env
        await provider._sync_devices()
        provider._connected = True
        await provider.call_service("light", "turn_on", "xiaodu.台灯", {})
        await provider.call_service("climate", "set_temperature", "xiaodu.云佳",
                                    {"temperature": 26})
        await provider.call_service("climate", "set_hvac_mode", "xiaodu.云佳",
                                    {"hvac_mode": "cool"})
        await provider.call_service("climate", "set_hvac_mode", "xiaodu.云佳",
                                    {"hvac_mode": "off"})
        iot = [c for c in calls if c[1] == "IOT_CONTROL_DEVICES"]
        assert iot[0][2] == {
            "applianceName": "台灯", "roomName": "书房", "action": "turnOn",
        }
        assert iot[1][2]["attribute"] == "temperature"
        assert iot[1][2]["value"] == "26"
        assert iot[2][2]["value"] == "制冷"
        assert iot[3][2]["action"] == "turnOff"

    @pytest.mark.asyncio
    async def test_speaker_commands(self, env) -> None:
        provider, calls = env
        await provider._sync_devices()
        provider._connected = True
        device = next(d for d in provider.snapshot() if d.entity_id == "xiaodu.c1")
        await provider.speak(device, "你好")
        await provider.call_service("media_player", "set_volume", "xiaodu.c1",
                                    {"volume_level": 0.4})
        speak = [c for c in calls if c[1] == "xiaodu_speak"]
        ctrl = [c for c in calls if c[1] == "control_xiaodu"]
        assert speak[0][2] == {"text": "你好", "cuid": "c1", "client_id": "k1"}
        assert ctrl[0][2]["command"] == "音量调到40"

    @pytest.mark.asyncio
    async def test_unconfigured_when_server_disabled(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        from entities.smart_home.providers import xiaodu as xiaodu_mod
        monkeypatch.setattr(xiaodu_mod, "bridge_server_status", lambda: [
            {"name": "xiaodu-iot", "enabled": True, "connected": True},
            {"name": "xiaodu", "enabled": False, "connected": False},
        ])
        assert xiaodu_mod.XiaoduProvider().is_configured() is False
