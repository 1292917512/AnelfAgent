"""节日数据与法定假日解析 — datetime 组件的数据层（纯函数 + 按年缓存）。

法定节假日由 holidays 库（python-holidays，CN 日历含农历节日与调休）提供，
常见固定日期纪念日（教师节/植树节等非法定节日）由内置表补充。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

import holidays

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# 常见固定日期纪念日（holidays 库 CN 日历不含的非法定节日），(月, 日) -> 名称
OBSERVANCES: Dict[Tuple[int, int], str] = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (3, 12): "植树节",
    (4, 1): "愚人节",
    (5, 4): "青年节",
    (6, 1): "儿童节",
    (7, 1): "建党节",
    (8, 1): "建军节",
    (9, 10): "教师节",
    (10, 31): "万圣夜",
    (11, 11): "光棍节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}

# 法定假日主名（holidays 输出带"（补假）""（调休）"等后缀，注入时取主名）
HOLIDAY_MAIN_NAMES = ("国庆节", "春节", "劳动节", "中秋节", "端午节", "清明节", "元旦")

_holiday_cache: Dict[int, holidays.HolidayBase] = {}


def country_holidays_cn(year: int) -> holidays.HolidayBase:
    """指定年份的中国法定节假日表（按年缓存，zh_CN 名称）。"""
    if year not in _holiday_cache:
        _holiday_cache[year] = holidays.country_holidays(
            "CN", years=[year], language="zh_CN",
        )
    return _holiday_cache[year]


def main_name(raw: str) -> str:
    """从 holidays 原始名称提取主节日名（去补假/调休/休息日噪音）。"""
    for name in HOLIDAY_MAIN_NAMES:
        if raw.startswith(name):
            return name
    return ""


def festivals_on(day: date) -> List[str]:
    """指定日期的节日名列表（法定假日主名 + 固定纪念日）。"""
    names: List[str] = []
    raw = country_holidays_cn(day.year).get(day)
    if raw:
        main = main_name(str(raw))
        if main:
            names.append(main)
    observance = OBSERVANCES.get((day.month, day.day))
    if observance and observance not in names:
        names.append(observance)
    return names


def holiday_start(year: int, main: str) -> Optional[date]:
    """某主节名在该年法定表中的最早日期（补假/连休日与正日归并为同一起点）。"""
    starts = [
        day for day, raw in country_holidays_cn(year).items()
        if str(raw).startswith(main)
    ]
    return min(starts) if starts else None


def next_holiday(today: date, days: int) -> Optional[Tuple[date, str]]:
    """未来 days 天内最近的尚未开始的法定假日主名。

    主名当年正日 ≤ today 视为「已开启」，其连休/补假残段不再预告——否则
    假期中段会误报倒计时（2026-10-04 bug：报「距国庆节还有 1 天（10-05）」，
    实际国庆 10-01 已开始）。跨年查次年表时正日即起点，不受影响。
    """
    for offset in range(1, days + 1):
        day = date.fromordinal(today.toordinal() + offset)
        raw = country_holidays_cn(day.year).get(day)
        if not raw:
            continue
        main = main_name(str(raw))
        if not main:
            continue
        start = holiday_start(day.year, main)
        if start is not None and start <= today:
            continue
        return day, main
    return None
