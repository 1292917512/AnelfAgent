"""日历 AI 工具 — calendar_manage：日程/标注的增删改查 + ICS 订阅管理。

提醒联动：带 remind_minutes 的事件在写入时经 _sdk 桥接注册 mind 持久化
提醒（reminders.json），到期由心跳拉起一轮完整 REPLY（重启不丢失）；
更新/删除事件时同步取消旧提醒。ICS 订阅事件只读，不支持本地改动。
"""

from __future__ import annotations

import json
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from core.tool_errors import ErrorCause
from entities._sdk import (
    add_persistent_reminder,
    cancel_persistent_reminder,
    save_config_value,
    tool,
    tool_error,
)

from ... import framework
from . import store
from .module import CalendarModule

# 全天事件提醒的基准时刻（当日 09:00 减去提前分钟数触发）
_ALL_DAY_BASE_HOUR = 9


def _get_module() -> CalendarModule:
    """日历组件实例（框架注册表解析，类型收窄）。"""
    module = framework.get_module("calendar")
    assert isinstance(module, CalendarModule)
    return module


def _validate_date(text: str) -> Optional[str]:
    """校验 YYYY-MM-DD，合法返回原文，非法返回 None。"""
    try:
        datetime.strptime(text.strip(), "%Y-%m-%d")
        return text.strip()
    except ValueError:
        return None


def _validate_time(text: str) -> Optional[str]:
    """校验 HH:MM（空串原样返回表示全天），非法返回 None。"""
    text = text.strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%H:%M")
        return text
    except ValueError:
        return None


def _event_start_ts(event: Dict[str, Any]) -> float:
    """事件开始时间戳（全天事件按当日 09:00 计）。"""
    clock = event.get("time") or f"{_ALL_DAY_BASE_HOUR:02d}:00"
    return datetime.strptime(
        f"{event['date']} {clock}", "%Y-%m-%d %H:%M").timestamp()


async def _link_reminder(event: Dict[str, Any]) -> Dict[str, Any]:
    """按 remind_minutes 为事件注册持久化提醒，回填 reminder_id。"""
    remind = event.get("remind_minutes")
    if not remind:
        return event
    remind_ts = _event_start_ts(event) - int(remind) * 60
    if remind_ts <= time.time():
        event["remind_minutes"] = None
        return event
    clock = event.get("time") or "全天"
    note = f"日程提醒：{event['title']}（{event['date']} {clock}）"
    if event.get("note"):
        note += f"，备注：{event['note']}"
    reminder_id = await add_persistent_reminder(note, remind_ts)
    event["reminder_id"] = reminder_id or None
    if not reminder_id:
        event["remind_minutes"] = None
    return event


async def _unlink_reminder(event: Dict[str, Any]) -> None:
    """取消事件关联的持久化提醒（若有）。"""
    reminder_id = event.get("reminder_id")
    if reminder_id:
        await cancel_persistent_reminder(str(reminder_id))


def _event_out(event: Dict[str, Any]) -> Dict[str, Any]:
    """事件输出视图（剥离内部字段）。"""
    return {k: v for k, v in event.items()
            if k not in ("created_ts", "updated_ts")}


def _subscriptions_key() -> str:
    return _get_module().full_config_key("subscriptions")


def _save_subscriptions(subs: list) -> None:
    save_config_value(_subscriptions_key(), json.dumps(subs, ensure_ascii=False))


@tool(name="calendar_manage", group="ai_desktop")
async def calendar_manage(
    action: str = "list",
    event_id: str = "",
    title: str = "",
    date: str = "",
    time: str = "",
    end_time: str = "",
    note: str = "",
    kind: str = "event",
    remind_minutes: int = -1,
    days: int = 14,
    name: str = "",
    url: str = "",
) -> str:
    """管理日历日程：list 查看日程/标注，add 添加日程或日子标注（kind=note 为标注，remind_minutes>0 时到点自动唤起你处理），update/remove 修改删除，subscribe/unsubscribe 管理外部日历订阅（ICS 链接，iCloud/Google 等共享地址），sync 立即同步订阅。

    Args:
        action: list（默认）/ add / update / remove / subscribe / unsubscribe / sync
        event_id: 事件 ID（update/remove 必填，list 返回中可见）
        title: 标题（add 必填）
        date: 日期 YYYY-MM-DD（add 必填；list 时为起始日，默认今天）
        time: 时间 HH:MM（留空为全天事件）
        end_time: 结束时间 HH:MM（可选）
        note: 备注或标注内容
        kind: event（日程，默认）/ note（日子标注，全天、不提醒）
        remind_minutes: 提前提醒分钟数（>0 生效，到点自动唤醒你；0/-1 不提醒）
        days: list 向后查看天数（默认 14）
        name: 订阅名（subscribe/unsubscribe 必填）
        url: 订阅地址（subscribe 必填；webcal:// 自动转 https，需授权的源在 URL 内嵌 user:pass）
    """
    action = action.strip().lower()

    if action == "list":
        return _list_events(date, days)
    if action == "add":
        return await _add(title, date, time, end_time, note, kind, remind_minutes)
    if action == "update":
        return await _update(event_id, title, date, time, end_time, note,
                             remind_minutes)
    if action == "remove":
        return await _remove(event_id)
    if action == "subscribe":
        return _subscribe(name, url)
    if action == "unsubscribe":
        return _unsubscribe(name)
    if action == "sync":
        success = await framework.force_refresh("calendar")
        return json.dumps({"success": success}, ensure_ascii=False)
    return tool_error(
        f"未知操作: {action}（可用: list / add / update / remove / "
        f"subscribe / unsubscribe / sync）",
        cause=ErrorCause.PARAM,
    )


def _list_events(date_text: str, days: int) -> str:
    """列出日程（含订阅来源与提醒标记）。"""
    start = _validate_date(date_text) if date_text.strip() else None
    start_day = date_cls.fromisoformat(start) if start else date_cls.today()
    days = max(1, min(days, 90))
    events = store.events_between(start_day, start_day + timedelta(days=days))
    return json.dumps(
        {"events": [_event_out(e) for e in events], "count": len(events),
         "range": {"start": start_day.isoformat(), "days": days}},
        ensure_ascii=False, default=str)


async def _add(title: str, date_text: str, time_text: str, end_time: str,
               note: str, kind: str, remind_minutes: int) -> str:
    """添加日程或日子标注。"""
    if not title.strip():
        return tool_error("add 需要提供 title 参数", cause=ErrorCause.PARAM)
    day = _validate_date(date_text)
    if day is None:
        return tool_error(f"日期格式非法: {date_text or '(未提供)'}（需 YYYY-MM-DD）",
                          cause=ErrorCause.PARAM)
    clock = _validate_time(time_text)
    if clock is None:
        return tool_error(f"时间格式非法: {time_text}（需 HH:MM）",
                          cause=ErrorCause.PARAM)
    if kind not in ("event", "note"):
        return tool_error(f"kind 仅支持 event / note: {kind}", cause=ErrorCause.PARAM)
    if kind == "note":
        clock = ""  # 标注一律全天、不提醒
        remind_minutes = -1
    end_clock = _validate_time(end_time) if end_time.strip() else ""
    if end_time.strip() and end_clock is None:
        return tool_error(f"结束时间格式非法: {end_time}（需 HH:MM）",
                          cause=ErrorCause.PARAM)

    event = await _link_reminder({
        "title": title.strip(),
        "date": day,
        "time": clock or None,
        "end_time": end_clock or None,
        "kind": kind,
        "note": note.strip(),
        "remind_minutes": remind_minutes if remind_minutes > 0 else None,
    })
    saved = store.add_event(event)
    return json.dumps({"success": True, "event": _event_out(saved)},
                      ensure_ascii=False, default=str)


async def _update(event_id: str, title: str, date_text: str, time_text: str,
                  end_time: str, note: str, remind_minutes: int) -> str:
    """更新事件字段（仅本地事件；提醒随字段变更重建）。"""
    event_id = event_id.strip()
    if not event_id:
        return tool_error("update 需要提供 event_id 参数", cause=ErrorCause.PARAM)
    current = next((e for e in store.load_events() if e.get("id") == event_id), None)
    if current is None:
        return tool_error(f"事件不存在: {event_id}", cause=ErrorCause.NOT_FOUND)
    if str(current.get("source", "local")).startswith("ics:"):
        return tool_error("订阅日历事件为只读，请在来源日历中修改",
                          cause=ErrorCause.PARAM)

    patch: Dict[str, Any] = {}
    if title.strip():
        patch["title"] = title.strip()
    if date_text.strip():
        day = _validate_date(date_text)
        if day is None:
            return tool_error(f"日期格式非法: {date_text}（需 YYYY-MM-DD）",
                              cause=ErrorCause.PARAM)
        patch["date"] = day
    if time_text.strip():
        clock = _validate_time(time_text)
        if clock is None:
            return tool_error(f"时间格式非法: {time_text}（需 HH:MM）",
                              cause=ErrorCause.PARAM)
        patch["time"] = clock or None
    if end_time.strip():
        end_clock = _validate_time(end_time)
        if end_clock is None:
            return tool_error(f"结束时间格式非法: {end_time}（需 HH:MM）",
                              cause=ErrorCause.PARAM)
        patch["end_time"] = end_clock or None
    if note.strip():
        patch["note"] = note.strip()
    if remind_minutes >= 0:
        patch["remind_minutes"] = remind_minutes or None
    if not patch:
        return tool_error("没有需要更新的字段", cause=ErrorCause.PARAM)

    merged = {**current, **patch}
    await _unlink_reminder(current)
    merged["reminder_id"] = None
    merged = await _link_reminder(merged)
    patch["reminder_id"] = merged.get("reminder_id")
    patch["remind_minutes"] = merged.get("remind_minutes")
    saved = store.update_event(event_id, patch)
    return json.dumps({"success": True, "event": _event_out(saved)},
                      ensure_ascii=False, default=str)


async def _remove(event_id: str) -> str:
    """删除事件（联动取消其提醒；订阅事件只读拒绝）。"""
    event_id = event_id.strip()
    if not event_id:
        return tool_error("remove 需要提供 event_id 参数", cause=ErrorCause.PARAM)
    current = next((e for e in store.load_events() if e.get("id") == event_id), None)
    if current is None:
        return tool_error(f"事件不存在: {event_id}", cause=ErrorCause.NOT_FOUND)
    if str(current.get("source", "local")).startswith("ics:"):
        return tool_error("订阅日历事件为只读，请删除对应订阅源",
                          cause=ErrorCause.PARAM)
    await _unlink_reminder(current)
    store.remove_event(event_id)
    return json.dumps({"success": True, "removed": event_id}, ensure_ascii=False)


def _subscribe(name: str, url: str) -> str:
    """新增 ICS 订阅源（保存配置即触发后台即时同步）。"""
    name, url = name.strip(), url.strip()
    if not name or not url:
        return tool_error("subscribe 需要提供 name 与 url 参数",
                          cause=ErrorCause.PARAM)
    subs = [dict(s) for s in _get_module().subscriptions()]
    if any(s["name"] == name for s in subs):
        return tool_error(f"订阅已存在: {name}", cause=ErrorCause.PARAM)
    subs.append({"name": name, "url": url, "enabled": True})
    _save_subscriptions(subs)
    return json.dumps({"success": True, "added": name,
                       "hint": "已保存，后台数秒内自动同步"}, ensure_ascii=False)


def _unsubscribe(name: str) -> str:
    """移除订阅源及其同步进来的全部事件。"""
    name = name.strip()
    subs = [dict(s) for s in _get_module().subscriptions()]
    kept = [s for s in subs if s["name"] != name]
    if len(kept) == len(subs):
        known = [s["name"] for s in subs]
        return tool_error(
            f"订阅不存在: {name or '(未提供)'}（当前: {', '.join(known) or '空'}）",
            cause=ErrorCause.NOT_FOUND,
        )
    _save_subscriptions(kept)
    store.replace_source_events(f"ics:{name}", [])
    return json.dumps({"success": True, "removed": name}, ensure_ascii=False)
