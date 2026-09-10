"""日程事件存储 — calendar 模块的数据层（JSON 文件 + mtime 缓存 + 原子写）。

事件模型::

    {"id": 8 位 hex, "title": 标题, "date": "YYYY-MM-DD", "time": "HH:MM"|null,
     "end_time": "HH:MM"|null, "kind": "event"|"note"(日子标注),
     "note": 备注, "remind_minutes": int|null, "reminder_id": 联动 reminders.json 的 id,
     "source": "local"|"ics:<订阅名>", "external_uid": ICS UID|null,
     "created_ts": float, "updated_ts": float}

存储文件为 ConfigPaths.CALENDAR（config/calendar.json，随配置目录搬迁）；
ICS 订阅同步按 source 整源替换（外部日历为只读事实源），本地事件不受影响。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths

_LOG_TAG = "AI桌面"

# 过期事件保留窗口：结束超过该天数的非订阅事件在写入时自动清理
_RETENTION_DAYS = 90

_cache: Dict[str, Any] = {"mtime": 0.0, "events": []}


def _path() -> Path:
    return Path(ConfigPaths.CALENDAR)


def _invalidate() -> None:
    """写入后失效缓存（下次读取重新加载）。"""
    _cache["mtime"] = 0.0


def load_events() -> List[Dict[str, Any]]:
    """读取全部事件（mtime 缓存：文件未变时零解析，供 render 零 I/O 调用）。"""
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if mtime == _cache["mtime"]:
        return list(_cache["events"])
    try:
        data = json.loads(path.read_text("utf-8"))
        events = [e for e in data.get("events", []) if isinstance(e, dict)]
    except Exception as exc:
        log(f"日历事件加载失败: {exc}", "WARNING", tag=_LOG_TAG)
        return []
    _cache["mtime"] = mtime
    _cache["events"] = events
    return list(events)


def save_events(events: List[Dict[str, Any]]) -> None:
    """原子写入全部事件（先写临时文件再替换），并清理过期事件。"""
    cutoff = date.today().toordinal() - _RETENTION_DAYS
    kept: List[Dict[str, Any]] = []
    for event in events:
        # ICS 订阅事件由同步整源替换管理，不参与过期清理
        if event.get("source", "local") == "local":
            try:
                if date.fromisoformat(str(event.get("date", ""))).toordinal() < cutoff:
                    continue
            except ValueError:
                continue  # 日期非法的事件不保留
        kept.append(event)

    path = _path()
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"events": kept}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as exc:
        log(f"日历事件保存失败: {exc}", "ERROR", tag=_LOG_TAG)
    finally:
        _invalidate()


def new_id() -> str:
    """事件 id（8 位 hex，与 reminders 同风格）。"""
    return uuid.uuid4().hex[:8]


def add_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """追加事件（补 id/时间戳后落盘），返回完整记录。"""
    now = time.time()
    event = {
        "id": new_id(),
        "kind": "event",
        "time": None,
        "end_time": None,
        "note": "",
        "remind_minutes": None,
        "reminder_id": None,
        "source": "local",
        "external_uid": None,
        **event,
        "created_ts": now,
        "updated_ts": now,
    }
    events = load_events()
    events.append(event)
    save_events(events)
    return event


def update_event(event_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按 id 更新事件字段，返回更新后的记录（不存在返回 None）。"""
    events = load_events()
    for event in events:
        if event.get("id") == event_id:
            event.update(patch)
            event["updated_ts"] = time.time()
            save_events(events)
            return event
    return None


def remove_event(event_id: str) -> Optional[Dict[str, Any]]:
    """按 id 删除事件，返回被删记录（不存在返回 None）。"""
    events = load_events()
    kept = [e for e in events if e.get("id") != event_id]
    if len(kept) == len(events):
        return None
    removed = next(e for e in events if e.get("id") == event_id)
    save_events(kept)
    return removed


def replace_source_events(source: str, events: List[Dict[str, Any]]) -> None:
    """整源替换某 ICS 订阅的事件（保留其他来源与本地事件）。"""
    now = time.time()
    normalized: List[Dict[str, Any]] = []
    for event in events:
        normalized.append({
            "id": new_id(),
            "kind": "event",
            "time": None,
            "end_time": None,
            "note": "",
            "remind_minutes": None,
            "reminder_id": None,
            "created_ts": now,
            **event,
            "source": source,
            "updated_ts": now,
        })
    others = [e for e in load_events() if e.get("source") != source]
    save_events(others + normalized)


def events_between(start: date, end: date) -> List[Dict[str, Any]]:
    """日期区间 [start, end] 内的事件（按日期+时间排序）。"""
    result: List[Dict[str, Any]] = []
    for event in load_events():
        try:
            day = date.fromisoformat(str(event.get("date", "")))
        except ValueError:
            continue
        if start.toordinal() <= day.toordinal() <= end.toordinal():
            result.append(event)
    return sorted(result, key=lambda e: (str(e.get("date")), str(e.get("time") or "")))
