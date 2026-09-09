"""地区检索服务 — Open-Meteo Geocoding API（weather 组件与 AI 工具/Web 路由共用）。

检索确认制的核心：地区必须先检索出候选、确认后带坐标落库，
采集时直接使用坐标，从机制上杜绝名称歧义导致的错配城市。
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 15.0


def search_locations(query: str, count: int = 8) -> List[Dict[str, Any]]:
    """检索地区候选（同步；供 Web 检索框与 AI 工具共用）。

    返回 [{name, label, latitude, longitude, admin1, country}]，
    label 为 "名称·行政区·国家" 组合；无结果返回空列表。
    """
    query = query.strip()
    if not query:
        return []
    with httpx.Client(timeout=HTTP_TIMEOUT, trust_env=False) as client:
        resp = client.get(
            GEOCODE_URL,
            params={"name": query, "count": count, "language": "zh", "format": "json"},
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    candidates: List[Dict[str, Any]] = []
    for item in results:
        parts = [item.get("name", query)]
        if item.get("admin1") and item["admin1"] != item.get("name"):
            parts.append(item["admin1"])
        if item.get("country") and item["country"] not in parts:
            parts.append(item["country"])
        candidates.append({
            "name": str(item.get("name", query)),
            "label": "·".join(parts),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "admin1": item.get("admin1") or "",
            "country": item.get("country") or "",
        })
    return candidates
