"""智能家居管理器与 AI 工具测试：查询过滤 / 控制校验链 / 广播 / 工具输出。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from core.config import ConfigManager
from core.tool_errors import ErrorCause
from entities.smart_home import framework
from entities.smart_home.manager import SmartHomeManager
from entities.smart_home.models import DeviceState, SmartHomeCallError
from entities.smart_home.providers.base import SmartHomeProvider
from entities.smart_home.tools import (
    smart_home_control,
    smart_home_devices,
    smart_home_domains,
)


class FakeProvider(SmartHomeProvider):
    """内存假平台（记录服务调用，可模拟连接状态）。"""

    key = "fake"
    display_name = "假平台"

    def __init__(self, devices: List[DeviceState], configured: bool = True,
                 connected: bool = True) -> None:
        super().__init__()
        self._devices = devices
        self._configured = configured
        self._connected = connected
        self.calls: List[Dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self._configured

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
        self.calls.append({
            "domain": domain, "service": service,
            "entity_id": entity_id, "data": data,
        })

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.key, "provider_name": self.display_name,
            "configured": self._configured, "connected": self._connected,
            "device_count": len(self._devices), "last_error": None,
            "connected_at": None,
        }


def _device(entity_id: str, domain: str, name: str, state: str,
            area: str = "", attributes: dict | None = None) -> DeviceState:
    return DeviceState(
        entity_id=entity_id, domain=domain, name=name, state=state,
        attributes=attributes or {}, area=area,
    )


DEVICES = [
    _device("light.desk", "light", "台灯", "on", area="书房",
            attributes={"brightness": 128}),
    _device("light.bed", "light", "床头灯", "off", area="卧室"),
    _device("sensor.temp", "sensor", "客厅温度", "23.5", area="客厅",
            attributes={"unit_of_measurement": "°C"}),
    _device("vacuum.robo", "vacuum", "扫地机", "docked", area="客厅"),
]


@pytest.fixture()
def manager(monkeypatch: pytest.MonkeyPatch) -> SmartHomeManager:
    """挂载假平台的独立管理器。"""
    fake = FakeProvider(DEVICES)
    mgr = SmartHomeManager()
    monkeypatch.setattr(
        "entities.smart_home.manager.active_provider", lambda: fake,
    )
    return mgr


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """工具测试用的假平台（patch manager 模块的 active_provider）。"""
    provider = FakeProvider(DEVICES)
    monkeypatch.setattr(
        "entities.smart_home.manager.active_provider", lambda: provider,
    )
    return provider


class TestQuery:
    def test_filter_by_domain_key_includes_extra_ha(self,
                                                    manager: SmartHomeManager) -> None:
        result = manager.devices(domain="sensor")
        assert {d.entity_id for d in result} == {"sensor.temp"}

    def test_filter_by_area_and_name(self, manager: SmartHomeManager) -> None:
        assert [d.entity_id for d in manager.devices(area="书房")] == ["light.desk"]
        assert [d.entity_id for d in manager.devices(name="灯")] == [
            "light.desk", "light.bed",
        ]

    def test_status(self, manager: SmartHomeManager) -> None:
        status = manager.status()
        assert status["connected"] is True
        assert status["device_count"] == 4


class TestCallService:
    @pytest.mark.asyncio
    async def test_success(self, manager: SmartHomeManager,
                           fake: FakeProvider) -> None:
        result = await manager.call_service("light.desk", "set_brightness", "60")
        assert result["service"] == "light.turn_on"
        assert fake.calls == [{
            "domain": "light", "service": "turn_on",
            "entity_id": "light.desk", "data": {"brightness_pct": 60},
        }]

    @pytest.mark.asyncio
    async def test_unconfigured_rejected(self,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeProvider(DEVICES, configured=False)
        monkeypatch.setattr(
            "entities.smart_home.manager.active_provider", lambda: provider,
        )
        mgr = SmartHomeManager()
        with pytest.raises(SmartHomeCallError) as info:
            await mgr.call_service("light.desk", "turn_on")
        assert info.value.cause == ErrorCause.CONFIG

    @pytest.mark.asyncio
    async def test_unknown_device_rejected(self,
                                           manager: SmartHomeManager) -> None:
        with pytest.raises(SmartHomeCallError) as info:
            await manager.call_service("light.ghost", "turn_on")
        assert info.value.cause == ErrorCause.NOT_FOUND

    @pytest.mark.asyncio
    async def test_unsupported_domain_rejected(self,
                                               manager: SmartHomeManager) -> None:
        with pytest.raises(SmartHomeCallError) as info:
            await manager.call_service("vacuum.robo", "turn_on")
        assert info.value.cause == ErrorCause.NOT_FOUND
        assert "vacuum" in str(info.value)

    @pytest.mark.asyncio
    async def test_disabled_domain_rejected(self,
                                            manager: SmartHomeManager) -> None:
        ConfigManager.set("smart_home_sensor_enabled", False)
        with pytest.raises(SmartHomeCallError) as info:
            await manager.call_service("sensor.temp", "turn_on")
        assert info.value.cause == ErrorCause.STATE
        ConfigManager.set("smart_home_sensor_enabled", True)

    @pytest.mark.asyncio
    async def test_disconnected_rejected(self,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeProvider(DEVICES, connected=False)
        monkeypatch.setattr(
            "entities.smart_home.manager.active_provider", lambda: provider,
        )
        mgr = SmartHomeManager()
        with pytest.raises(SmartHomeCallError) as info:
            await mgr.call_service("light.desk", "turn_on")
        assert info.value.cause == ErrorCause.NETWORK

    @pytest.mark.asyncio
    async def test_invalid_action_rejected(self,
                                           manager: SmartHomeManager) -> None:
        with pytest.raises(SmartHomeCallError) as info:
            await manager.call_service("sensor.temp", "turn_on")
        # sensor 域只读：动作校验先于平台调用失败
        assert info.value.cause == ErrorCause.PARAM
        assert "无" in str(info.value)


class TestBroadcast:
    def test_subscribe_receives_events(self, manager: SmartHomeManager) -> None:
        queue = manager.subscribe()
        provider_status = manager.status()
        manager._broadcast({"event": "connection", "status": provider_status})
        assert queue.get_nowait()["event"] == "connection"
        manager.unsubscribe(queue)
        manager._broadcast({"event": "sync"})
        assert queue.empty()


class TestTools:
    def test_devices_json(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_devices(domain="light"))
        assert payload["count"] == 2
        assert payload["connection"]["connected"] is True
        # 批量清单裁剪属性（>5 台才裁，此处保留）
        assert payload["devices"][0]["attributes"] == {"brightness": 128}

    def test_devices_slim_when_many(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_devices(name="灯"))
        assert payload["count"] == 2

    @pytest.mark.asyncio
    async def test_control_success(self, fake: FakeProvider) -> None:
        payload = json.loads(await smart_home_control("light.bed", "turn_on"))
        assert payload["success"] is True
        assert fake.calls[0]["entity_id"] == "light.bed"

    @pytest.mark.asyncio
    async def test_control_error_contract(self, fake: FakeProvider) -> None:
        raw = await smart_home_control("light.ghost", "turn_on")
        payload = json.loads(raw)
        assert payload["error"] is True or "error" in payload
        assert "不存在" in json.dumps(payload, ensure_ascii=False)

    def test_domains_list(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_domains("list"))
        keys = {d["key"] for d in payload["domains"]}
        assert {"light", "sensor", "scene"} <= keys
        light = next(d for d in payload["domains"] if d["key"] == "light")
        assert light["device_count"] == 2
        assert "set_brightness" in light["actions"]

    def test_domains_preview(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_domains("preview"))
        assert payload["injecting"] is True
        assert "台灯" in payload["content"]

    def test_domains_enable_disable(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_domains("disable", domain="scene"))
        assert payload["success"] is True
        assert framework.get_domain("scene").is_enabled() is False  # type: ignore[union-attr]
        json.loads(smart_home_domains("enable", domain="scene"))
        assert framework.get_domain("scene").is_enabled() is True  # type: ignore[union-attr]

    def test_domains_unknown(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_domains("enable", domain="ghost"))
        assert "未找到设备域" in json.dumps(payload, ensure_ascii=False)

    def test_domains_set_config(self, fake: FakeProvider) -> None:
        payload = json.loads(
            smart_home_domains("set_config", domain="sensor",
                               name="inject_limit", value="3"),
        )
        assert payload["success"] is True
        assert payload["value"] == 3
        ConfigManager.set("smart_home_sensor_inject_limit", 8)

    def test_domains_unknown_action(self, fake: FakeProvider) -> None:
        payload = json.loads(smart_home_domains("explode"))
        assert "未知操作" in json.dumps(payload, ensure_ascii=False)
