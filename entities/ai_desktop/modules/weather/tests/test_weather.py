"""天气组件测试：WMO 码映射 / 风向转换 / 多地区模型 / 刷新调度（无网络）。"""

from __future__ import annotations

import json

import pytest

from core.config import ConfigManager
from entities.ai_desktop.modules.weather.codes import weather_text, wind_direction_text
from entities.ai_desktop.modules.weather.locations import parse_locations
from entities.ai_desktop.modules.weather.module import WeatherModule

_SAMPLE = {
    "label": "北京", "condition": "晴", "temp": 26.0, "feels": 28.0,
    "humidity": 45.0, "wind_speed": 12.0, "wind_direction": "西南",
    "today_min": 20.0, "today_max": 31.0, "fetched_at": 1.0,
}


def _set_locations(locations: list) -> None:
    ConfigManager.set("ai_desktop_weather_locations",
                      json.dumps(locations, ensure_ascii=False))


class TestWmoMapping:
    def test_known_codes(self) -> None:
        assert weather_text(0) == "晴"
        assert weather_text(95) == "雷暴"
        assert weather_text("61") == "小雨"

    def test_unknown_code_fallback(self) -> None:
        assert weather_text(999) == "天气多变"
        assert weather_text(None) == "天气多变"


class TestWindDirection:
    def test_eight_directions(self) -> None:
        assert wind_direction_text(0) == "北"
        assert wind_direction_text(90) == "东"
        assert wind_direction_text(225) == "西南"
        assert wind_direction_text(359) == "北"

    def test_invalid_input(self) -> None:
        assert wind_direction_text(None) == ""
        assert wind_direction_text("abc") == ""


class TestParseLocations:
    def test_json_string(self) -> None:
        raw = '[{"name": "北京", "enabled": true}, {"name": "上海", "enabled": false}]'
        assert parse_locations(raw) == [
            {"name": "北京", "enabled": True},
            {"name": "上海", "enabled": False},
        ]

    def test_entries_with_coordinates(self) -> None:
        """检索确认落库的条目（label/经纬度）完整解析。"""
        raw = json.dumps([{
            "name": "北京", "label": "北京·北京市·中国",
            "latitude": 39.9, "longitude": 116.4, "enabled": True,
        }])
        entry = parse_locations(raw)[0]
        assert entry["latitude"] == 39.9
        assert entry["label"] == "北京·北京市·中国"

    def test_plain_string_items(self) -> None:
        assert parse_locations('["北京", "上海"]') == [
            {"name": "北京", "enabled": True},
            {"name": "上海", "enabled": True},
        ]

    def test_invalid_falls_back_to_default(self) -> None:
        assert parse_locations("not-json") == [{"name": "北京", "enabled": True}]
        assert parse_locations(123) == [{"name": "北京", "enabled": True}]
        assert parse_locations("") == [{"name": "北京", "enabled": True}]

    def test_explicit_empty_list_respected(self) -> None:
        """显式空列表 "[]" 表示不关注任何地区（不回落默认）。"""
        assert parse_locations("[]") == []


class TestMultiLocation:
    @pytest.mark.asyncio
    async def test_refresh_renders_one_line_per_location(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_locations([
            {"name": "北京", "enabled": True},
            {"name": "上海", "enabled": True},
            {"name": "广州", "enabled": False},
        ])
        module = WeatherModule()
        monkeypatch.setattr(module, "_fetch_all", lambda entries: {
            e["name"]: {**_SAMPLE, "label": e["name"]} for e in entries
        })
        await module.refresh()
        text = module.render()
        assert text is not None
        lines = text.split("\n")
        assert len(lines) == 2  # 广州停用不注入
        assert lines[0].startswith("[天气] 北京：今日 晴 20~31°C，现在 26°C（体感 28°C）")
        assert lines[1].startswith("[天气] 上海：")

    @pytest.mark.asyncio
    async def test_location_error_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单地区失败收敛为 error 条目，不影响其他地区注入。"""
        _set_locations([
            {"name": "北京", "enabled": True},
            {"name": "不存在市", "enabled": True},
        ])
        module = WeatherModule()
        monkeypatch.setattr(module, "_fetch_all", lambda entries: {
            "北京": dict(_SAMPLE),
            "不存在市": {"error": "未找到地区: 不存在市"},
        })
        await module.refresh()
        text = module.render()
        assert text == "[天气] 北京：今日 晴 20~31°C，现在 26°C（体感 28°C），湿度 45%，西南风 12km/h"
        detail = module.detail()
        error_entry = next(l for l in detail["locations"] if l["name"] == "不存在市")
        assert error_entry["error"] == "未找到地区: 不存在市"
        assert error_entry["has_data"] is False

    @pytest.mark.asyncio
    async def test_stale_location_data_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从配置移除/停用地区后，其缓存数据被清理。"""
        module = WeatherModule()
        _set_locations([{"name": "北京", "enabled": True}])
        monkeypatch.setattr(module, "_fetch_all", lambda entries: {
            e["name"]: dict(_SAMPLE) for e in entries
        })
        await module.refresh()
        assert "北京" in module._data
        _set_locations([{"name": "上海", "enabled": True}])
        await module.refresh()
        assert "北京" not in module._data
        assert "上海" in module._data


class TestDue:
    def test_due_immediately_without_data(self) -> None:
        module = WeatherModule()
        assert module.due(1000000.0) is True

    def test_interval_from_config(self) -> None:
        _set_locations([{"name": "北京", "enabled": True}])
        module = WeatherModule()
        module._data = {"北京": dict(_SAMPLE)}
        module.last_refresh = 1000.0
        ConfigManager.set("ai_desktop_weather_refresh_minutes", 30)
        assert module.due(1000.0 + 1799) is False
        assert module.due(1000.0 + 1801) is True

    def test_missing_location_data_triggers_due(self) -> None:
        """新增启用地区但尚无数据时立即到期（配置变更即时生效的兜底）。"""
        _set_locations([
            {"name": "北京", "enabled": True},
            {"name": "上海", "enabled": True},
        ])
        module = WeatherModule()
        module._data = {"北京": dict(_SAMPLE)}
        module.last_refresh = 1000.0
        ConfigManager.set("ai_desktop_weather_refresh_minutes", 30)
        assert module.due(1000.0 + 60) is True

    def test_interval_clamped_to_minimum(self) -> None:
        _set_locations([{"name": "北京", "enabled": True}])
        module = WeatherModule()
        module._data = {"北京": dict(_SAMPLE)}
        module.last_refresh = 1000.0
        ConfigManager.set("ai_desktop_weather_refresh_minutes", 1)
        # 低于 10 分钟下限按 10 分钟计
        assert module.due(1000.0 + 599) is False
        assert module.due(1000.0 + 601) is True

class TestCoordinateFirstFetch:
    def test_stored_coordinates_skip_geocoding(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """带坐标的条目直接按坐标采集，不再走名称地理编码（杜绝错配城市）。"""
        module = WeatherModule()

        def _boom(query: str) -> None:
            raise AssertionError("不应触发地理编码")

        monkeypatch.setattr(module, "_resolve_location", _boom)

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "current": {"weather_code": 0, "temperature_2m": 20.0},
                    "daily": {"temperature_2m_max": [25.0],
                              "temperature_2m_min": [15.0]},
                }

        class _Client:
            def get(self, url: str, params: dict) -> _Resp:
                assert url.endswith("/v1/forecast")
                assert params["latitude"] == 39.9
                return _Resp()

        entry = {"name": "北京", "label": "北京·北京市·中国",
                 "latitude": 39.9, "longitude": 116.4, "enabled": True}
        data = module._fetch_one(_Client(), entry)
        assert data["label"] == "北京·北京市·中国"
        assert data["condition"] == "晴"
        assert data["temp"] == 20.0


class TestAiToolLocationOps:
    """AI 工具的地区管理（检索确认落库 / 移除 / 类型矫正）。"""

    def test_add_location_resolves_and_stores_coordinates(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from entities.ai_desktop import tools as ai_tools

        monkeypatch.setattr(ai_tools, "search_locations", lambda q, count=8: [{
            "name": q, "label": f"{q}·某省·中国",
            "latitude": 30.5, "longitude": 114.3,
            "admin1": "某省", "country": "中国",
        }])
        out = json.loads(ai_tools.ai_desktop_modules("add_location", name="武汉"))
        assert out["success"] is True
        assert out["added"]["latitude"] == 30.5
        stored = parse_locations(ConfigManager.get("ai_desktop_weather_locations"))
        assert stored[-1]["name"] == "武汉"
        assert stored[-1]["latitude"] == 30.5

    def test_add_location_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from entities.ai_desktop import tools as ai_tools

        monkeypatch.setattr(ai_tools, "search_locations", lambda q, count=8: [])
        out = json.loads(ai_tools.ai_desktop_modules("add_location", name="火星市"))
        assert out["cause"] == "not_found"

    def test_add_duplicate_rejected(self) -> None:
        from entities.ai_desktop import tools as ai_tools

        _set_locations([{"name": "北京", "enabled": True}])
        out = json.loads(ai_tools.ai_desktop_modules("add_location", name="北京"))
        assert "已存在" in out["error"]

    def test_remove_location(self) -> None:
        from entities.ai_desktop import tools as ai_tools

        _set_locations([{"name": "北京", "enabled": True}])
        out = json.loads(ai_tools.ai_desktop_modules("remove_location", name="北京"))
        assert out["success"] is True
        assert parse_locations(ConfigManager.get("ai_desktop_weather_locations")) == []

    def test_set_config_coerces_types(self) -> None:
        """set_config 经统一配置架构矫正类型与边界（不写入裸字符串）。"""
        from entities.ai_desktop import tools as ai_tools

        out = json.loads(ai_tools.ai_desktop_modules(
            "set_config", module="weather", name="refresh_minutes", value="45"))
        assert out["value"] == 45
        assert ConfigManager.get("ai_desktop_weather_refresh_minutes") == 45

        # 越界值按声明边界收敛（min=10）
        out = json.loads(ai_tools.ai_desktop_modules(
            "set_config", module="weather", name="refresh_minutes", value="1"))
        assert out["value"] == 10


class TestDailyForecast:
    """7 日预报解析与注入行明日摘要。"""

    _DAILY = {
        "time": ["2026-09-10", "2026-09-11", "2026-09-12"],
        "weather_code": [0, 61, 95],
        "temperature_2m_max": [30.1, 28.0, 25.4],
        "temperature_2m_min": [15.0, 17.2, 18.8],
        "precipitation_probability_max": [0, 65, 90],
    }

    def test_parse_daily(self) -> None:
        forecast = WeatherModule._parse_daily(self._DAILY)
        assert len(forecast) == 3
        assert forecast[0] == {"date": "2026-09-10", "condition": "晴",
                               "tmin": 15.0, "tmax": 30.1, "precip_prob": 0.0}
        assert forecast[1]["condition"] == "小雨"
        assert forecast[2]["precip_prob"] == 90.0

    def test_parse_daily_empty(self) -> None:
        assert WeatherModule._parse_daily({}) == []

    def test_render_line_with_tomorrow(self) -> None:
        """明日摘要进入注入行；降水概率过半时附降水提示。"""
        data = {**_SAMPLE, "forecast": WeatherModule._parse_daily(self._DAILY)}
        line = WeatherModule._format_line(data)
        assert "｜明日 小雨 17~28°C 降水65%" in line

    def test_render_line_without_forecast(self) -> None:
        line = WeatherModule._format_line(_SAMPLE)
        assert "明日" not in line

    def test_forecast_for(self) -> None:
        _set_locations([{"name": "北京", "enabled": True}])
        module = WeatherModule()
        module._data = {"北京": {**_SAMPLE, "label": "北京",
                                 "forecast": WeatherModule._parse_daily(self._DAILY)}}
        result = module.forecast_for("北京", 2)
        assert len(result["北京"]) == 2
        assert module.forecast_for("不存在", 5) == {}
