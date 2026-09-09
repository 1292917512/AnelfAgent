"""WMO 天气码与风向的文本映射 — weather 组件的展示词汇层。"""

from __future__ import annotations

from typing import Any, Optional

# WMO Weather interpretation codes（https://open-meteo.com/en/docs）→ 中文
WMO_CODES: dict[int, str] = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "浓毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "雷暴伴冰雹",
}

WIND_DIRECTIONS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")


def weather_text(code: Any) -> str:
    """WMO 天气码转中文描述（未知码返回通用词）。"""
    try:
        return WMO_CODES.get(int(code), "天气多变")
    except (TypeError, ValueError):
        return "天气多变"


def wind_direction_text(degrees: Any) -> str:
    """风向角度转八方位中文（非法输入返回空串）。"""
    try:
        index = int((float(degrees) + 22.5) // 45) % 8
    except (TypeError, ValueError):
        return ""
    return WIND_DIRECTIONS[index]


def to_number(value: Any) -> Optional[float]:
    """数值字段归一（非法值归 None 而非抛错）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
