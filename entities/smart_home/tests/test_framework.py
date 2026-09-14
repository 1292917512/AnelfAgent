"""设备域框架测试：装饰器注册 / 配置生成 / 注入组装 / 动作解析。"""

from __future__ import annotations

import pytest

from core.config import ConfigManager
from core.tool_errors import ErrorCause
from entities.smart_home import framework
from entities.smart_home.framework import DeviceDomain, device_domain
from entities.smart_home.models import ActionSpec, DeviceState, SmartHomeCallError


@device_domain
class _DummyDomain(DeviceDomain):
    """测试用设备域。"""

    key = "dummy_test"
    display_name = "测试设备"
    description = "框架单测专用"
    priority = 99
    ha_domains = ("dummy",)
    config_schema = {
        **DeviceDomain.config_schema,
        "suffix": {"description": "注入后缀", "default": ""},
    }

    def actions(self):
        return {
            "turn_on": ActionSpec("turn_on", "打开"),
            "set_level": ActionSpec(
                "set_level", "设置档位", value_param="level",
                value_hint="档位 1-5", convert=int,
            ),
        }


def _device(
    entity_id: str = "dummy.alpha",
    name: str = "甲设备",
    state: str = "on",
    domain: str = "dummy",
    attributes: dict | None = None,
    area: str = "",
) -> DeviceState:
    return DeviceState(
        entity_id=entity_id, domain=domain, name=name, state=state,
        attributes=attributes or {}, area=area,
    )


class TestRegistration:
    def test_decorator_registers_instance(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        assert domain.display_name == "测试设备"
        assert framework.domain_for_ha("dummy") is domain

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            @device_domain
            class _NoKey(DeviceDomain):
                key = ""
                ha_domains = ("x",)

    def test_missing_ha_domains_rejected(self) -> None:
        with pytest.raises(ValueError):
            @device_domain
            class _NoHa(DeviceDomain):
                key = "no_ha"

    def test_all_domains_sorted_by_priority(self) -> None:
        priorities = [d.priority for d in framework.all_domains()]
        assert priorities == sorted(priorities)

    def test_builtin_domains_registered(self) -> None:
        keys = {d.key for d in framework.all_domains()}
        assert {"light", "switch", "climate", "sensor", "cover",
                "media_player", "scene"} <= keys


class TestConfigEntries:
    def test_keys_prefixed(self) -> None:
        entries = framework.config_entries()
        assert entries["smart_home_dummy_test_enabled"]["default"] is True
        assert entries["smart_home_dummy_test_suffix"]["default"] == ""
        assert "smart_home_dummy_test_inject_limit" in entries

    def test_get_config_falls_back_to_schema_default(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        assert domain.get_config("suffix") == ""
        ConfigManager.set("smart_home_dummy_test_suffix", "!")
        assert domain.get_config("suffix") == "!"


class TestRender:
    def test_renders_domain_line(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        line = domain.render([_device(), _device("dummy.beta", "乙设备", "off")])
        assert line == "测试设备: 乙设备: 关 | 甲设备: 开"  # 按名称排序（乙 < 甲 码点序）

    def test_inert_and_foreign_devices_skipped(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        devices = [
            _device("dummy.down", "离线设备", "unavailable"),
            _device("light.x", "其他域", domain="light"),
        ]
        assert domain.render(devices) is None

    def test_inject_limit_truncates(self) -> None:
        ConfigManager.set("smart_home_dummy_test_inject_limit", 2)
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        devices = [_device(f"dummy.d{i}", f"设备{i}") for i in range(4)]
        line = domain.render(devices)
        assert line is not None
        assert "等 4 台" in line
        ConfigManager.set("smart_home_dummy_test_inject_limit", 8)


class TestRenderContext:
    def test_aggregates_enabled_domains(self) -> None:
        ConfigManager.set("smart_home_dummy_test_enabled", True)
        content = framework.render_context([_device()])
        assert content.startswith("[智能家居]")
        assert "测试设备: 甲设备: 开" in content

    def test_disabled_domain_excluded(self) -> None:
        ConfigManager.set("smart_home_dummy_test_enabled", False)
        assert "测试设备" not in framework.render_context([_device()])
        ConfigManager.set("smart_home_dummy_test_enabled", True)

    def test_empty_when_no_devices(self) -> None:
        assert framework.render_context([]) == ""


class TestBuildServiceCall:
    def test_simple_action(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        call = domain.build_service_call("turn_on")
        assert call.ha_domain is None
        assert call.service == "turn_on"
        assert call.data == {}

    def test_value_action_converted(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        call = domain.build_service_call("set_level", "3")
        assert call.service == "set_level"
        assert call.data == {"level": 3}

    def test_unknown_action_rejected(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        with pytest.raises(SmartHomeCallError) as info:
            domain.build_service_call("explode")
        assert info.value.cause == ErrorCause.PARAM
        assert "turn_on" in str(info.value)

    def test_missing_value_rejected(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        with pytest.raises(SmartHomeCallError) as info:
            domain.build_service_call("set_level", "")
        assert info.value.cause == ErrorCause.PARAM

    def test_invalid_value_rejected(self) -> None:
        domain = framework.get_domain("dummy_test")
        assert domain is not None
        with pytest.raises(SmartHomeCallError) as info:
            domain.build_service_call("set_level", "abc")
        assert info.value.cause == ErrorCause.PARAM


class TestBuiltinDomainFormats:
    def test_light_brightness(self) -> None:
        domain = framework.get_domain("light")
        assert domain is not None
        device = _device("light.desk", "台灯", "on", domain="light",
                         attributes={"brightness": 128, "color_temp_kelvin": 4000})
        assert domain.format_state(device) == "开 · 亮度 50% · 色温 4000K"

    def test_climate_full(self) -> None:
        domain = framework.get_domain("climate")
        assert domain is not None
        device = _device("climate.room", "空调", "cool", domain="climate",
                         attributes={"temperature": 24,
                                     "current_temperature": 26.5,
                                     "current_humidity": 45})
        assert domain.format_state(device) == "制冷 · 24°C · 室温 26.5°C · 湿度 45%"

    def test_sensor_unit(self) -> None:
        domain = framework.get_domain("sensor")
        assert domain is not None
        device = _device("sensor.temp", "温度", "23.5", domain="sensor",
                         attributes={"unit_of_measurement": "°C"})
        assert domain.format_state(device) == "23.5°C"
        assert domain.actions() == {}

    def test_light_brightness_percent_clamped(self) -> None:
        domain = framework.get_domain("light")
        assert domain is not None
        with pytest.raises(SmartHomeCallError):
            domain.build_service_call("set_brightness", "120")
        call = domain.build_service_call("set_brightness", "60")
        assert (call.service, call.data) == ("turn_on", {"brightness_pct": 60})

    def test_media_player_volume_mapped(self) -> None:
        domain = framework.get_domain("media_player")
        assert domain is not None
        call = domain.build_service_call("set_volume", "40")
        assert (call.service, call.data) == ("volume_set", {"volume_level": 0.4})

    def test_media_player_speak_cross_domain(self) -> None:
        domain = framework.get_domain("media_player")
        assert domain is not None
        ConfigManager.set("smart_home_media_player_tts_service", "tts.cloud_say")
        call = domain.build_service_call("speak", "主人，任务完成了")
        assert call.ha_domain == "tts"
        assert call.service == "cloud_say"
        assert call.data == {"message": "主人，任务完成了"}
        ConfigManager.set("smart_home_media_player_tts_service", "")

    def test_media_player_speak_requires_tts_service(self) -> None:
        domain = framework.get_domain("media_player")
        assert domain is not None
        ConfigManager.set("smart_home_media_player_tts_service", "")
        with pytest.raises(SmartHomeCallError) as info:
            domain.build_service_call("speak", "你好")
        assert info.value.cause == ErrorCause.CONFIG
        assert "tts_service" in str(info.value)

    def test_media_player_speak_requires_text(self) -> None:
        domain = framework.get_domain("media_player")
        assert domain is not None
        with pytest.raises(SmartHomeCallError) as info:
            domain.build_service_call("speak", "")
        assert info.value.cause == ErrorCause.PARAM

    def test_media_player_play_media(self) -> None:
        domain = framework.get_domain("media_player")
        assert domain is not None
        call = domain.build_service_call("play_media", "https://example.com/a.mp3")
        assert call.ha_domain is None
        assert call.service == "play_media"
        assert call.data == {
            "media_content_id": "https://example.com/a.mp3",
            "media_content_type": "music",
        }

    def test_scene_name_only(self) -> None:
        domain = framework.get_domain("scene")
        assert domain is not None
        device = _device("scene.movie", "观影模式", "2026-09-13T10:00:00",
                         domain="scene")
        assert domain.format_device(device) == "观影模式"
