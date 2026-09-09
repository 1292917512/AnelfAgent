"""日期时间组件 — 当前时间、星期、法定节假日与额外关注时区注入。

即时型组件（render 时直接计算，零 I/O）：主时区一行 + 每个额外时区一行，
节日数据来自 festivals 数据层（holidays 库 + 内置纪念表）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ...framework import DesktopModule, desktop_module
from . import festivals


@desktop_module
class DatetimeModule(DesktopModule):
    """注入当前日期时间与节日信息（今天是什么节日、下一个法定假日倒计时）。"""

    key = "datetime"
    display_name = "日期时间"
    description = "当前时间、星期、法定假日与常见纪念日（含下一节日倒计时与额外时区）"
    priority = 10
    refresh_interval = 0.0
    config_schema = {
        "timezone": {
            "description": "主时区（IANA 名，如 Asia/Shanghai；留空用系统本地时区）",
            "default": "",
        },
        "extra_timezones": {
            "description": "额外关注时区（逗号分隔的 IANA 名，如 Asia/Tokyo,America/New_York；每个时区注入一行时间）",
            "default": "",
        },
        "upcoming_days": {
            "description": "预告未来多少天内的法定假日",
            "default": 14,
            "min": 1,
            "max": 60,
            "unit": "天",
            "advanced": True,
        },
    }

    def _now(self) -> datetime:
        """按配置时区取当前时间（配置非法时回落系统本地）。"""
        return self._now_with_tz()[0]

    def _now_with_tz(self) -> Tuple[datetime, str]:
        """当前时间 + 实际使用的时区名（配置非法/留空时为系统本地）。"""
        tz_name = str(self.get_config("timezone", "") or "").strip()
        if tz_name:
            try:
                return datetime.now(ZoneInfo(tz_name)), tz_name
            except Exception:
                pass
        return datetime.now().astimezone(), "系统本地"

    def _extra_zones(self) -> List[Tuple[str, Optional[ZoneInfo]]]:
        """额外时区列表（配置逗号分隔；非法时区以 (原名, None) 保留供面板报错）。"""
        raw = str(self.get_config("extra_timezones", "") or "")
        zones: List[Tuple[str, Optional[ZoneInfo]]] = []
        for name in raw.split(","):
            name = name.strip()
            if not name:
                continue
            try:
                zones.append((name, ZoneInfo(name)))
            except Exception:
                zones.append((name, None))
        return zones

    @staticmethod
    def _zone_line(name: str, tz: ZoneInfo) -> str:
        """额外时区的注入行（城市名取 IANA 名末段）。"""
        now = datetime.now(tz)
        city = name.split("/")[-1].replace("_", " ")
        return (
            f"[时间] {city} {now.date().isoformat()} "
            f"{festivals.WEEKDAYS[now.weekday()]} {now.strftime('%H:%M')}（{name}）"
        )

    def _upcoming_days(self) -> int:
        """预告窗口天数（配置非法时回落默认）。"""
        try:
            return max(1, int(self.get_config("upcoming_days", 14)))
        except (TypeError, ValueError):
            return 14

    def render(self) -> Optional[str]:
        now = self._now()
        today = now.date()
        parts = [
            f"[时间] {today.isoformat()} {festivals.WEEKDAYS[today.weekday()]} "
            f"{now.strftime('%H:%M')}",
        ]

        today_festivals = festivals.festivals_on(today)
        upcoming = festivals.next_holiday(today, self._upcoming_days())

        notes: List[str] = []
        if today_festivals:
            notes.append("今天是" + "、".join(today_festivals))
        if upcoming is not None:
            day, name = upcoming
            delta = (day - today).days
            notes.append(f"距{name}还有 {delta} 天（{day.strftime('%m-%d')}）")
        if notes:
            parts.append("[节日] " + "；".join(notes))
        for tz_name, tz in self._extra_zones():
            if tz is not None:
                parts.append(self._zone_line(tz_name, tz))
        return "\n".join(parts)

    def detail(self) -> Dict[str, Any]:
        """面板展示用的结构化时间详情（当前时间/时区/今日节日/下一假日/额外时区）。"""
        now, tz_label = self._now_with_tz()
        today = now.date()
        upcoming = festivals.next_holiday(today, self._upcoming_days())
        extras: List[Dict[str, Any]] = []
        for tz_name, tz in self._extra_zones():
            if tz is None:
                extras.append({"timezone": tz_name, "valid": False})
            else:
                znow = datetime.now(tz)
                extras.append({
                    "timezone": tz_name,
                    "valid": True,
                    "datetime": znow.strftime("%Y-%m-%d %H:%M:%S"),
                    "weekday": festivals.WEEKDAYS[znow.weekday()],
                })
        return {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": festivals.WEEKDAYS[today.weekday()],
            "timezone": tz_label,
            "festivals_today": festivals.festivals_on(today),
            "next_holiday": (
                {
                    "name": upcoming[1],
                    "date": upcoming[0].isoformat(),
                    "days": (upcoming[0] - today).days,
                }
                if upcoming else None
            ),
            "extra_zones": extras,
        }
