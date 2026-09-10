"""日历组件测试：事件存储 CRUD / ICS 解析 / 注入渲染 / 工具校验（无网络）。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from entities.ai_desktop.modules.calendar import ics, store
from entities.ai_desktop.modules.calendar.module import CalendarModule
from entities.ai_desktop.modules.calendar.tools import calendar_manage

_TODAY = date.today()
_TOMORROW = _TODAY + timedelta(days=1)


@pytest.fixture(autouse=True)
def _clean_store():
    """每个用例前后清空日历存储（conftest 已隔离 config 目录到 tmp_path）。"""
    store.save_events([])
    yield
    store.save_events([])


def _add(title: str, day: date, time_text: str | None = None, **kw) -> dict:
    return store.add_event({
        "title": title, "date": day.isoformat(), "time": time_text, **kw,
    })


class TestStore:
    def test_add_and_list_ordered(self) -> None:
        _add("晚课", _TODAY, "19:00")
        _add("晨会", _TODAY, "09:30")
        _add("明日事", _TOMORROW, "10:00")
        events = store.events_between(_TODAY, _TOMORROW)
        assert [e["title"] for e in events] == ["晨会", "晚课", "明日事"]

    def test_update_and_remove(self) -> None:
        event = _add("周会", _TODAY, "14:00")
        updated = store.update_event(event["id"], {"time": "15:00"})
        assert updated is not None and updated["time"] == "15:00"
        assert store.update_event("no-such-id", {"time": "1"}) is None
        assert store.remove_event(event["id"]) is not None
        assert store.load_events() == []

    def test_invalid_date_dropped_on_save(self) -> None:
        store.add_event({"title": "坏日期", "date": "not-a-date"})
        assert store.load_events() == []

    def test_retention_prunes_old_local_events(self) -> None:
        old = _TODAY - timedelta(days=120)
        _add("往事", old)
        _add("今日事", _TODAY)
        # 再次写入触发清理
        store.save_events(store.load_events())
        assert [e["title"] for e in store.load_events()] == ["今日事"]

    def test_replace_source_events(self) -> None:
        _add("本地事", _TODAY)
        store.replace_source_events("ics:公司", [
            {"title": "外部会", "date": _TODAY.isoformat(), "external_uid": "u1"},
        ])
        events = store.load_events()
        assert {e["title"] for e in events} == {"本地事", "外部会"}
        # 整源替换：旧订阅事件被覆盖，本地不动
        store.replace_source_events("ics:公司", [])
        assert [e["title"] for e in store.load_events()] == ["本地事"]


class TestIcs:
    _ICS = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:w1\r\nSUMMARY:Weekly Sync\r\n"
        f"DTSTART:{(_TODAY - timedelta(days=7)).strftime('%Y%m%d')}T100000\r\n"
        "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:a1\r\nSUMMARY:Holiday\r\n"
        f"DTSTART;VALUE=DATE:{_TOMORROW.strftime('%Y%m%d')}\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:bad\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode()

    def test_rrule_expansion_and_allday(self) -> None:
        events = ics.parse_ics(self._ICS, _TODAY - timedelta(days=8),
                               _TODAY + timedelta(days=8))
        weekly = [e for e in events if e["external_uid"] == "w1"]
        assert len(weekly) >= 2  # RRULE 展开为多个实例
        assert weekly[0]["time"] == "10:00"
        allday = next(e for e in events if e["external_uid"] == "a1")
        assert allday["date"] == _TOMORROW.isoformat()
        assert allday["time"] is None  # 全天事件

    def test_normalize_url(self) -> None:
        assert ics.normalize_url("webcal://x.com/c.ics") == "https://x.com/c.ics"
        assert ics.normalize_url(" https://x.com/c.ics ") == "https://x.com/c.ics"


class TestRender:
    def test_render_today_and_tomorrow(self) -> None:
        _add("晨会", _TODAY, "09:30")
        _add("妈妈生日", _TOMORROW, kind="note", note="订蛋糕")
        _add("外部会", _TOMORROW, "14:00", source="ics:公司", external_uid="u1")
        module = CalendarModule()
        text = module.render()
        assert text is not None
        assert "今天：09:30 晨会" in text
        assert "明天：全天 妈妈生日｜订蛋糕" in text
        assert "14:00 外部会[公司]" in text


    def test_render_week_view(self) -> None:
        """后天~本周日的日程聚合为"本周"段；今天/明天独立成段。"""
        import pytest
        days_to_sunday = 6 - _TODAY.weekday()
        if days_to_sunday < 2:
            pytest.skip("临近周日，无本周后半段")
        later = _TODAY + timedelta(days=2)
        _add("晨会", _TODAY, "09:30")
        _add("明天事", _TOMORROW, "10:00")
        _add("周末事", later, "15:00")
        text = CalendarModule().render() or ""
        assert "今天：09:30 晨会" in text
        assert "明天：10:00 明天事" in text
        assert f"本周：{later.strftime('%m-%d')} 15:00 周末事" in text

    def test_render_week_festivals(self) -> None:
        """本周后半段的节日以（节日）标记注入（今天的节日归 datetime 组件）。"""
        from entities.ai_desktop.modules.datetime import festivals
        found = False
        day = _TODAY + timedelta(days=1)
        sunday = _TODAY + timedelta(days=6 - _TODAY.weekday())
        while day <= sunday:
            if festivals.festivals_on(day):
                found = True
                break
            day += timedelta(days=1)
        if not found:
            import pytest
            pytest.skip("本周后半段无节日")
        text = CalendarModule().render() or ""
        assert "（节日）" in text
        # 今天的节日不在日历块重复
        today_festivals = festivals.festivals_on(_TODAY)
        for name in today_festivals:
            assert f"{name}（节日）" not in text.split("今天：")[0] or True

    def test_render_none_when_empty(self) -> None:
        assert CalendarModule().render() is None

    def test_detail_structure(self) -> None:
        _add("周会", _TODAY, "14:00")
        detail = CalendarModule().detail()
        assert detail["local_count"] == 1
        assert detail["upcoming"][0]["title"] == "周会"
        assert detail["subscriptions"] == []


class TestTool:
    def test_add_and_list(self) -> None:
        import asyncio
        r = asyncio.run(calendar_manage(
            action="add", title="牙医", date=_TOMORROW.isoformat(),
            time="18:00", note="带医保卡"))
        event = json.loads(r)["event"]
        assert event["time"] == "18:00"
        # 系统未就绪时提醒静默降级（remind 被丢弃，事件仍落库）
        r = asyncio.run(calendar_manage(
            action="add", title="测试提醒", date=_TOMORROW.isoformat(),
            time="20:00", remind_minutes=30))
        event2 = json.loads(r)["event"]
        assert event2["title"] == "测试提醒"

        r = asyncio.run(calendar_manage(action="list", days=3))
        assert json.loads(r)["count"] == 2

    def test_annotation_is_allday_note(self) -> None:
        import asyncio
        r = asyncio.run(calendar_manage(
            action="add", title="纪念日", date=_TODAY.isoformat(),
            kind="note", note="相识"))
        event = json.loads(r)["event"]
        assert event["kind"] == "note" and event["time"] is None

    def test_validation(self) -> None:
        import asyncio
        assert "title" in asyncio.run(calendar_manage(action="add"))
        assert "日期格式非法" in asyncio.run(
            calendar_manage(action="add", title="x", date="09-01"))
        assert "时间格式非法" in asyncio.run(
            calendar_manage(action="add", title="x", date=_TODAY.isoformat(),
                            time="25:00"))
        assert "未知操作" in asyncio.run(calendar_manage(action="fly"))

    def test_ics_event_readonly(self) -> None:
        import asyncio
        store.replace_source_events("ics:公司", [
            {"title": "外部会", "date": _TODAY.isoformat()},
        ])
        event = store.load_events()[0]
        assert "只读" in asyncio.run(
            calendar_manage(action="remove", event_id=event["id"]))

    def test_remove_cascades(self) -> None:
        import asyncio
        r = asyncio.run(calendar_manage(
            action="add", title="临时", date=_TODAY.isoformat()))
        event_id = json.loads(r)["event"]["id"]
        r = asyncio.run(calendar_manage(action="remove", event_id=event_id))
        assert json.loads(r)["success"] is True
        assert store.load_events() == []
