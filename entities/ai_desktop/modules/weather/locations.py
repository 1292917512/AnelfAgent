"""地区配置解析 — weather 组件的 locations 配置模型。

配置值（``ai_desktop_weather_locations``）为 JSON 列表，每条目
``{"name", "label", "latitude", "longitude", "enabled"}``：label/坐标由
检索确认流程（geocode.search_locations → Web 候选选择 / AI add_location）
写入，采集时直接使用；缺失坐标的历史条目回落按名称地理编码。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .codes import to_number

DEFAULT_LOCATIONS: List[Dict[str, Any]] = [{"name": "北京", "enabled": True}]


def default_locations() -> List[Dict[str, Any]]:
    """默认地区列表（拷贝，防调用方改脏模块级常量）。"""
    return [dict(item) for item in DEFAULT_LOCATIONS]


def default_locations_json() -> str:
    """默认地区列表的 JSON 字符串（配置项默认值）。"""
    return json.dumps(DEFAULT_LOCATIONS, ensure_ascii=False)


def parse_locations(raw: Any) -> List[Dict[str, Any]]:
    """解析地区配置（JSON 列表字符串或原生列表）。

    未设置/非法输入回落默认地区；显式空列表 "[]" 表示不关注任何地区。
    """
    if isinstance(raw, str):
        if not raw.strip():
            return default_locations()
        try:
            value: Any = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return default_locations()
    else:
        value = raw
    if not isinstance(value, list):
        return default_locations()
    locations: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            locations.append({"name": item.strip(), "enabled": True})
        elif isinstance(item, dict) and str(item.get("name", "")).strip():
            entry: Dict[str, Any] = {
                "name": str(item["name"]).strip(),
                "enabled": bool(item.get("enabled", True)),
            }
            if item.get("label"):
                entry["label"] = str(item["label"])
            lat, lon = to_number(item.get("latitude")), to_number(item.get("longitude"))
            if lat is not None and lon is not None:
                entry["latitude"] = lat
                entry["longitude"] = lon
            locations.append(entry)
    return locations
