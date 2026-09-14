"""供应商组件测试：多供应商路由 / 小米组件 / 小度组件。"""

from __future__ import annotations

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


class TestXiaoduDiscovery:
    def test_parse_response(self) -> None:
        from entities.smart_home.providers.xiaodu import discovery
        raw = (
            "HTTP/1.1 200 OK\r\n"
            "LOCATION: http://192.168.1.20:49494/description.xml\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "USN: uuid:abc\r\n\r\n"
        ).encode()
        headers = discovery.parse_response(raw)
        assert headers["LOCATION"] == "http://192.168.1.20:49494/description.xml"
        assert discovery.is_xiaodu_location(headers["LOCATION"]) is True
        assert discovery.is_xiaodu_location("http://192.168.1.21:8008/ddd.xml") is False


class FakeSpeaker:
    """小度音箱控制会话假实现。"""

    def __init__(self, host: str, name: str = "小度音箱") -> None:
        self.host = host
        self.name = name
        self.played: List[str] = []
        self.volume: List[float] = []
        self.stopped = 0

    async def play_url(self, url: str, title: str) -> None:
        self.played.append(url)

    async def set_volume(self, level: float) -> None:
        self.volume.append(level)

    async def stop(self) -> None:
        self.stopped += 1

    async def poll(self) -> Dict[str, Any]:
        return {"state": "playing", "volume_level": 0.3, "media_title": ""}


class TestXiaoduProvider:
    @pytest.fixture()
    def provider(self, monkeypatch: pytest.MonkeyPatch):
        from entities.smart_home.providers import xiaodu as xiaodu_mod
        monkeypatch.setattr(xiaodu_mod.XiaoduSpeaker, "create", _fake_create)
        return xiaodu_mod.XiaoduProvider()

    def test_manual_locations(self, provider) -> None:
        from core.config import ConfigManager
        ConfigManager.set("smart_home_xiaodu_hosts",
                          "192.168.1.20, http://192.168.1.30:49494/description.xml")
        locations = provider._manual_locations()
        assert locations == [
            "http://192.168.1.20:49494/description.xml",
            "http://192.168.1.30:49494/description.xml",
        ]
        ConfigManager.set("smart_home_xiaodu_hosts", "")

    @pytest.mark.asyncio
    async def test_add_locations_registers_devices(self, provider) -> None:
        added = await provider._add_locations(
            ["http://192.168.1.20:49494/description.xml"],
        )
        assert added == ["xiaodu.192_168_1_20"]
        device = provider.snapshot()[0]
        assert device.domain == "media_player"
        # 重复接入只刷新会话不重复注册
        assert await provider._add_locations(
            ["http://192.168.1.20:49494/description.xml"],
        ) == []
        assert len(provider.snapshot()) == 1

    @pytest.mark.asyncio
    async def test_call_service_volume(self, provider) -> None:
        await provider._add_locations(
            ["http://192.168.1.20:49494/description.xml"],
        )
        await provider.call_service("media_player", "volume_set",
                                    "xiaodu.192_168_1_20",
                                    {"volume_level": 0.5})
        speaker = provider._speakers["xiaodu.192_168_1_20"]
        assert speaker.volume == [0.5]

    @pytest.mark.asyncio
    async def test_speak_full_link(self, provider) -> None:
        await provider._add_locations(
            ["http://192.168.1.20:49494/description.xml"],
        )

        class FakeTts:
            async def synthesize(self, text: str) -> str:
                return f"/cache/{text}.mp3"

        class FakeServer:
            def url_for(self, filename: str, peer: str) -> str:
                return f"http://192.168.1.2:8195/audio/{filename}"

        provider._synthesizer = FakeTts()
        provider._server = FakeServer()
        device = provider.snapshot()[0]
        assert provider.supports_speak(device) is True
        await provider.speak(device, "播报")
        speaker = provider._speakers["xiaodu.192_168_1_20"]
        assert speaker.played == ["http://192.168.1.2:8195/audio/播报.mp3"]


async def _fake_create(location: str) -> FakeSpeaker:
    """按 location 构造假音箱（host 从 URL 提取）。"""
    from yarl import URL
    return FakeSpeaker(URL(location).host or "")
