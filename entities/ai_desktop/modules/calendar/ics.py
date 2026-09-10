"""ICS 订阅同步 — calendar 模块的外部日历接入（只读，通用标准）。

覆盖全平台主流日历的"订阅/共享"通道：iCloud 日历公开链接、Google 日历
私密 ICS 地址、Outlook/Nextcloud 等——统一产出 ICS 订阅 URL（`webcal://`
自动转 `https://`；需要授权的源把账号/应用专用密码内嵌进 URL
`https://user:pass@host/...`，httpx 原生支持，免建凭据库）。

RRULE 重复事件经 recurring-ical-events 在同步窗口内展开为具体实例；
同步按 source 整源替换（store.replace_source_events），外部日历为
只读事实源，本地不可改。

后续若需双向同步（CalDAV 等），在本模块新增提供者实现 ``sync_subscription``
同签名函数即可，store/module 层无需变动。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

HTTP_TIMEOUT = 20.0


def normalize_url(url: str) -> str:
    """订阅地址归一：webcal:// → https://（各平台共享链接的常见 scheme）。"""
    url = url.strip()
    if url.lower().startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    return url


def _to_local_parts(dt_value: Any) -> tuple[str, Optional[str]]:
    """icalendar 的 dt/date 值 → (本地日期, 时间)；date 为全天事件。"""
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is not None:
            dt_value = dt_value.astimezone()
        return dt_value.date().isoformat(), dt_value.strftime("%H:%M")
    return dt_value.isoformat(), None


def parse_ics(content: bytes, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """解析 ICS 内容为事件列表（RRULE 在 [window_start, window_end] 内展开）。

    返回 store 事件模型子集：title/date/time/end_time/note/external_uid。
    单个 VEVENT 解析失败跳过不扩散；无 SUMMARY/DTSTART 的条目忽略。
    """
    import recurring_ical_events
    from icalendar import Calendar

    raw = Calendar.from_ical(content)
    # 预清洗：缺 SUMMARY/DTSTART 的残缺 VEVENT 会让 recurring_ical_events
    # 在展开期抛 KeyError（无法逐条跳过），先过滤再展开；VTIMEZONE 原样保留
    cal = Calendar()
    cal.add("VERSION", "2.0")
    for component in raw.walk():
        if component.name == "VTIMEZONE":
            cal.add_component(component)
        elif component.name == "VEVENT":
            if component.get("SUMMARY") is None or component.get("DTSTART") is None:
                continue
            cal.add_component(component)

    events: List[Dict[str, Any]] = []
    for component in recurring_ical_events.of(cal).between(window_start, window_end):
        summary = component.get("SUMMARY")
        dtstart = component.get("DTSTART")
        if summary is None or dtstart is None:
            continue
        try:
            start_date, start_time = _to_local_parts(dtstart.dt)
            end_time: Optional[str] = None
            dtend = component.get("DTEND")
            if dtend is not None and start_time is not None:
                end_date, end_part = _to_local_parts(dtend.dt)
                if end_date == start_date:
                    end_time = end_part
            description = component.get("DESCRIPTION")
            events.append({
                "title": str(summary).strip(),
                "date": start_date,
                "time": start_time,
                "end_time": end_time,
                "note": str(description).strip()[:500] if description else "",
                "external_uid": str(component.get("UID", "")) or None,
            })
        except Exception:
            continue
    return events


def sync_subscription(
    url: str, past_days: int = 30, future_days: int = 90,
) -> List[Dict[str, Any]]:
    """抓取并解析一个 ICS 订阅源（同步；经 asyncio.to_thread 调用）。

    展开窗口：过去 past_days 天 ~ 未来 future_days 天（重复事件实例化范围）。
    """
    url = normalize_url(url)
    with httpx.Client(timeout=HTTP_TIMEOUT, trust_env=False,
                      follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
    today = date.today()
    return parse_ics(
        content,
        today - timedelta(days=past_days),
        today + timedelta(days=future_days),
    )
