"""日期时间组件测试：节日数据层 / 倒计时 / 渲染输出 / 额外时区。"""

from __future__ import annotations

from datetime import date

from core.config import ConfigManager
from entities.ai_desktop.modules.datetime import festivals
from entities.ai_desktop.modules.datetime.module import DatetimeModule


class TestFestivals:
    def test_statutory_holiday(self) -> None:
        assert "国庆节" in festivals.festivals_on(date(2026, 10, 1))

    def test_fixed_observance(self) -> None:
        assert "教师节" in festivals.festivals_on(date(2026, 9, 10))

    def test_adjusted_rest_day_ignored(self) -> None:
        # 调休休息日（如"休息日（由 xxx 调休）"）不应产生节日名
        assert festivals.festivals_on(date(2026, 10, 6)) == []

    def test_plain_day_empty(self) -> None:
        assert festivals.festivals_on(date(2026, 9, 7)) == []


class TestNextHoliday:
    def test_nearest_within_window(self) -> None:
        assert festivals.next_holiday(date(2026, 9, 20), 14) == (
            date(2026, 9, 25), "中秋节",
        )

    def test_none_beyond_window(self) -> None:
        assert festivals.next_holiday(date(2026, 9, 26), 3) is None

    def test_cross_year(self) -> None:
        result = festivals.next_holiday(date(2026, 12, 30), 14)
        assert result == (date(2027, 1, 1), "元旦")


class TestRender:
    def test_render_contains_time_block(self) -> None:
        module = DatetimeModule()
        text = module.render()
        assert text is not None
        assert text.startswith("[时间] ")
        assert "星期" in text

    def test_render_festival_line_on_holiday(self) -> None:
        module = DatetimeModule()
        # 固定到国庆节，验证节日行注入
        from datetime import datetime
        fixed = datetime(2026, 10, 1, 9, 30).astimezone()
        module._now = lambda: fixed  # type: ignore[method-assign]
        text = module.render()
        assert text is not None
        assert "[节日] 今天是国庆节" in text


class TestExtraTimezones:
    def test_extra_zone_line_injected(self) -> None:
        ConfigManager.set("ai_desktop_datetime_extra_timezones", "Asia/Tokyo")
        module = DatetimeModule()
        text = module.render()
        assert text is not None
        lines = text.split("\n")
        tokyo = [line for line in lines if "Asia/Tokyo" in line]
        assert len(tokyo) == 1
        assert tokyo[0].startswith("[时间] Tokyo ")

    def test_invalid_zone_skipped_in_render_but_flagged_in_detail(self) -> None:
        ConfigManager.set("ai_desktop_datetime_extra_timezones", "Not/AZone")
        module = DatetimeModule()
        text = module.render()
        assert text is not None
        assert "Not/AZone" not in text
        detail = module.detail()
        extras = detail["extra_zones"]
        assert extras == [{"timezone": "Not/AZone", "valid": False}]

    def test_detail_extra_zones_valid(self) -> None:
        ConfigManager.set("ai_desktop_datetime_extra_timezones", "America/New_York")
        module = DatetimeModule()
        extras = module.detail()["extra_zones"]
        assert extras[0]["valid"] is True
        assert "datetime" in extras[0]
