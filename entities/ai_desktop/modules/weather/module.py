"""天气组件 — 基于 Open-Meteo 公开 API 的多地区实时天气注入。

Open-Meteo（https://open-meteo.com）是免费开源的天气数据项目，无需
API Key。多地区模型：``locations`` 配置为 JSON 列表（结构见 locations.py），
每个地区独立采集、独立缓存、独立容错（单地区失败收敛为该地区 error
条目，不影响其他地区），启用的地区各产出一行注入文本。
轮询型组件：后台按 ``refresh_minutes`` 间隔采集，render 只读缓存快照。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from ...framework import DesktopModule, desktop_module
from .codes import to_number, weather_text, wind_direction_text
from .geocode import FORECAST_URL, HTTP_TIMEOUT, search_locations
from .locations import default_locations_json, parse_locations


@desktop_module
class WeatherModule(DesktopModule):
    """注入配置地区的实时天气（多地区可配可独立启停，Open-Meteo 免 Key 公开数据源）。"""

    key = "weather"
    display_name = "天气"
    description = "多个地区的实时天气与当日气温（Open-Meteo 公开数据，无需密钥）"
    priority = 20
    refresh_interval = 1800.0
    config_schema = {
        "locations": {
            "description": "天气地区列表（JSON：[{\"name\": \"城市名\", \"enabled\": true}]；经检索确认后带坐标落库）",
            "default": default_locations_json(),
            "advanced": True,
        },
        "refresh_minutes": {
            "description": "天气刷新间隔",
            "default": 30,
            "min": 10,
            "max": 360,
            "step": 5,
            "unit": "分钟",
            "advanced": True,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        # 地区名 -> 天气数据 / {"error": 错误信息}
        self._data: Dict[str, Dict[str, Any]] = {}
        # 地区名 -> 地理编码结果（按名称缓存，仅无坐标的历史条目使用）
        self._geo_cache: Dict[str, Dict[str, Any]] = {}

    def _locations(self) -> List[Dict[str, Any]]:
        """当前配置的地区列表。"""
        return parse_locations(self.get_config("locations", ""))

    def enabled_location_names(self) -> List[str]:
        """启用地区名列表（工具报错提示用）。"""
        return [loc["name"] for loc in self._locations() if loc["enabled"]]

    def due(self, now: float) -> bool:
        """刷新间隔取配置值（热生效）；启用地区存在缺数据时立即到期。"""
        try:
            minutes = int(self.get_config("refresh_minutes", 30))
        except (TypeError, ValueError):
            minutes = 30
        interval = max(10, minutes) * 60.0
        if now - self.last_refresh >= interval:
            return True
        active = [loc["name"] for loc in self._locations() if loc["enabled"]]
        return any(name not in self._data for name in active)

    async def refresh(self) -> None:
        """后台拉取全部启用地区的天气（网络 I/O 放线程执行，单地区失败不扩散）。"""
        entries = [loc for loc in self._locations() if loc["enabled"]]
        if entries:
            results = await asyncio.to_thread(self._fetch_all, entries)
            self._data.update(results)
        # 清理已移除/停用地区的缓存数据
        active_names = {loc["name"] for loc in entries}
        stale = [name for name in self._data if name not in active_names]
        for name in stale:
            del self._data[name]

    def render(self) -> Optional[str]:
        """从结构化快照格式化注入文本（每个有数据的启用地区一行）。"""
        lines: List[str] = []
        for loc in self._locations():
            if not loc["enabled"]:
                continue
            data = self._data.get(loc["name"])
            if not data or "error" in data:
                continue
            lines.append(self._format_line(data))
        return "\n".join(lines) if lines else None

    def detail(self) -> Dict[str, Any]:
        """面板展示用的结构化详情（逐地区数据/错误 + 刷新配置）。"""
        locations: List[Dict[str, Any]] = []
        for loc in self._locations():
            data = self._data.get(loc["name"])
            entry: Dict[str, Any] = {
                "name": loc["name"],
                "enabled": loc["enabled"],
                "has_data": bool(data and "error" not in data),
                "error": data.get("error") if data else None,
            }
            if loc.get("label"):
                entry["configured_label"] = loc["label"]
            if data and "error" not in data:
                entry.update(data)
            locations.append(entry)
        return {
            "locations": locations,
            "refresh_minutes": self.get_config("refresh_minutes", 30),
        }

    @staticmethod
    def _format_line(data: Dict[str, Any]) -> str:
        """单地区数据 → 注入行文本（今日/明日两段式；降水概率过半时提示）。"""
        segments: List[str] = []
        if data.get("temp") is not None:
            temp_part = f"现在 {data['temp']:.0f}°C"
            if data.get("feels") is not None:
                temp_part += f"（体感 {data['feels']:.0f}°C）"
            segments.append(temp_part)
        if data.get("humidity") is not None:
            segments.append(f"湿度 {data['humidity']:.0f}%")
        if data.get("wind_speed") is not None:
            wind_part = f"{data['wind_direction']}风 " if data.get("wind_direction") else "风速 "
            segments.append(f"{wind_part}{data['wind_speed']:.0f}km/h")

        today_part = f"今日 {data['condition']}"
        if data.get("today_min") is not None and data.get("today_max") is not None:
            today_part += f" {data['today_min']:.0f}~{data['today_max']:.0f}°C"
        forecast = data.get("forecast") or []
        if forecast and forecast[0].get("precip_prob") is not None \
                and forecast[0]["precip_prob"] >= 50:
            today_part += f" 降水{forecast[0]['precip_prob']:.0f}%"
        segments.insert(0, today_part)
        line = f"[天气] {data['label']}：" + "，".join(segments)

        if len(forecast) > 1:
            tomorrow = forecast[1]
            part = f"｜明日 {tomorrow['condition']}"
            if tomorrow.get("tmin") is not None and tomorrow.get("tmax") is not None:
                part += f" {tomorrow['tmin']:.0f}~{tomorrow['tmax']:.0f}°C"
            precip = tomorrow.get("precip_prob")
            if precip is not None and precip >= 50:
                part += f" 降水{precip:.0f}%"
            line += part
        return line

    def forecast_for(self, name: str = "", days: int = 5) -> Dict[str, Any]:
        """逐日预报查询（AI 工具用）：name 为空时返回全部启用地区。"""
        days = max(1, min(days, 7))
        result: Dict[str, Any] = {}
        for loc in self._locations():
            if not loc["enabled"]:
                continue
            if name and loc["name"] != name:
                continue
            data = self._data.get(loc["name"])
            if not data or "error" in data:
                continue
            result[loc["name"]] = (data.get("forecast") or [])[:days]
        return result

    # ---- 以下为同步采集实现（经 asyncio.to_thread 调用） ----

    def _fetch_all(self, entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """逐地区采集（共享连接；单地区异常收敛为该地区的 error 条目）。"""
        results: Dict[str, Dict[str, Any]] = {}
        with httpx.Client(timeout=HTTP_TIMEOUT, trust_env=False) as client:
            for entry in entries:
                try:
                    results[entry["name"]] = self._fetch_one(client, entry)
                except Exception as exc:
                    results[entry["name"]] = {"error": str(exc)}
        return results

    def _resolve_location(self, query: str) -> Dict[str, Any]:
        """城市名 → 经纬度（按名称缓存；仅无坐标的历史条目回落到此路径）。"""
        cached = self._geo_cache.get(query)
        if cached is not None:
            return cached
        candidates = search_locations(query, count=1)
        if not candidates:
            raise ValueError(f"未找到地区: {query}")
        top = candidates[0]
        geo = {
            "latitude": top["latitude"],
            "longitude": top["longitude"],
            "label": top["label"],
        }
        self._geo_cache[query] = geo
        return geo

    def _fetch_one(self, client: httpx.Client, entry: Dict[str, Any]) -> Dict[str, Any]:
        """单地区天气采集与结构化（优先用检索确认时落库的坐标）。"""
        if entry.get("latitude") is not None and entry.get("longitude") is not None:
            geo = {
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
                "label": entry.get("label") or entry["name"],
            }
        else:
            geo = self._resolve_location(entry["name"])
        resp = client.get(
            FORECAST_URL,
            params={
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "current": "temperature_2m,relative_humidity_2m,"
                           "apparent_temperature,weather_code,"
                           "wind_speed_10m,wind_direction_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                         "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

        current = payload.get("current") or {}
        daily = payload.get("daily") or {}
        maxes = daily.get("temperature_2m_max") or []
        mins = daily.get("temperature_2m_min") or []
        return {
            "label": geo["label"],
            "condition": weather_text(current.get("weather_code")),
            "temp": to_number(current.get("temperature_2m")),
            "feels": to_number(current.get("apparent_temperature")),
            "humidity": to_number(current.get("relative_humidity_2m")),
            "wind_speed": to_number(current.get("wind_speed_10m")),
            "wind_direction": wind_direction_text(current.get("wind_direction_10m")),
            "today_max": to_number(maxes[0]) if maxes else None,
            "today_min": to_number(mins[0]) if mins else None,
            "forecast": self._parse_daily(daily),
            "fetched_at": time.time(),
        }

    @staticmethod
    def _parse_daily(daily: Dict[str, Any]) -> List[Dict[str, Any]]:
        """daily 段 → 逐日预报列表（date/condition/tmin/tmax/降水概率）。"""
        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        maxes = daily.get("temperature_2m_max") or []
        mins = daily.get("temperature_2m_min") or []
        precip = daily.get("precipitation_probability_max") or []
        forecast: List[Dict[str, Any]] = []
        for i, day in enumerate(dates):
            forecast.append({
                "date": str(day),
                "condition": weather_text(codes[i]) if i < len(codes) else "",
                "tmin": to_number(mins[i]) if i < len(mins) else None,
                "tmax": to_number(maxes[i]) if i < len(maxes) else None,
                "precip_prob": to_number(precip[i]) if i < len(precip) else None,
            })
        return forecast
