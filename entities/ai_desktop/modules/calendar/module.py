"""日历日程组件 — 今日/近期日程与日子标注注入 + ICS 订阅同步。

轮询型组件：后台按 ``sync_minutes`` 间隔同步 ICS 订阅源（只读外部日历），
render 从 store 的 mtime 缓存计算今明/近期日程（零网络 I/O）。
本地事件的提醒不在此触发——创建/更新时经 _sdk 桥接写入 mind 的持久化
提醒（reminders.json），到期由心跳拉起一轮完整 REPLY，重启不丢失。

Model Experience:
    模型看到什么 —— volatile 层 ``[日程]`` 行：今天/明天/本周剩余日程与标注
        （含时间、标题、订阅来源标记、本周节日）；AI 经 calendar_manage 工具增删改查；
    token 影响 —— 每轮增量注入，通常一行几十 token，范围以本周日为界自然有界；
    缓存影响 —— 走 volatile 尾部动态区，不触碰 stable/conversation 前缀缓存。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from ...framework import DesktopModule, desktop_module
from . import ics, store


@desktop_module
class CalendarModule(DesktopModule):
    """注入今日与近期日程（本地日程 + 日子标注 + ICS 订阅外部日历）。"""

    key = "calendar"
    display_name = "日历日程"
    description = "日程与日子标注（AI 可增删改、设提醒到点唤起），支持订阅外部 ICS 日历（iCloud/Google 等共享链接）"
    priority = 15
    refresh_interval = 3600.0
    config_schema = {
        "subscriptions": {
            "description": "外部日历订阅（ICS 链接 JSON：[{\"name\": \"名称\", \"url\": \"https://...\", \"enabled\": true}]；webcal:// 自动转 https，需授权的源在 URL 内嵌 user:pass）",
            "default": "[]",
            "advanced": True,
        },
        "sync_minutes": {
            "description": "外部日历同步间隔",
            "default": 60,
            "min": 15,
            "max": 1440,
            "step": 15,
            "unit": "分钟",
            "advanced": True,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        # 订阅名 -> 同步状态 {"ok": bool, "count": int, "error": str, "synced_at": float}
        self._sync_status: Dict[str, Dict[str, Any]] = {}

    # ---- 配置 ----

    def subscriptions(self) -> List[Dict[str, Any]]:
        """当前订阅源列表（配置非法时为空列表）。"""
        raw = self.get_config("subscriptions", "[]")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else []
            except (json.JSONDecodeError, ValueError):
                return []
        if not isinstance(raw, list):
            return []
        return [
            {"name": str(item.get("name", "")).strip(),
             "url": str(item.get("url", "")).strip(),
             "enabled": bool(item.get("enabled", True))}
            for item in raw
            if isinstance(item, dict) and str(item.get("name", "")).strip()
            and str(item.get("url", "")).strip()
        ]

    # ---- 调度（ICS 同步） ----

    def due(self, now: float) -> bool:
        """同步间隔取配置值（热生效）；启用订阅存在未同步源时立即到期。"""
        try:
            minutes = int(self.get_config("sync_minutes", 60))
        except (TypeError, ValueError):
            minutes = 60
        interval = max(15, minutes) * 60.0
        if now - self.last_refresh >= interval:
            return True
        active = [s["name"] for s in self.subscriptions() if s["enabled"]]
        return any(name not in self._sync_status for name in active)

    async def refresh(self) -> None:
        """逐订阅源同步（网络 I/O 放线程执行；单源失败收敛为该源状态）。"""
        subs = [s for s in self.subscriptions() if s["enabled"]]
        for sub in subs:
            try:
                events = await asyncio.to_thread(ics.sync_subscription, sub["url"])
                await asyncio.to_thread(
                    store.replace_source_events, f"ics:{sub['name']}", events)
                self._sync_status[sub["name"]] = {
                    "ok": True, "count": len(events), "error": "",
                    "synced_at": time.time(),
                }
            except Exception as exc:
                self._sync_status[sub["name"]] = {
                    "ok": False, "count": 0, "error": str(exc)[:120],
                    "synced_at": time.time(),
                }
        # 清理已移除/停用订阅的同步状态（其事件随下次整源替换清理）
        active = {s["name"] for s in subs}
        for name in [n for n in self._sync_status if n not in active]:
            del self._sync_status[name]
            await asyncio.to_thread(store.replace_source_events, f"ics:{name}", [])

    # ---- 渲染 ----

    @staticmethod
    def _event_text(event: Dict[str, Any]) -> str:
        """单事件 → "14:00 团队周会" / "全天 妈妈生日｜备注" 片段。"""
        time_part = event.get("time") or "全天"
        if event.get("end_time"):
            time_part += f"-{event['end_time']}"
        text = f"{time_part} {event.get('title', '')}"
        if event.get("kind") == "note" and event.get("note"):
            text += f"｜{event['note']}"
        elif event.get("note"):
            text += f"（{event['note']}）"
        source = str(event.get("source", "local"))
        if source.startswith("ics:"):
            text += f"[{source[4:]}]"
        return text

    def render(self) -> Optional[str]:
        """注入文本（周视图）：今天 / 明天 / 本周剩余（含节日，供 AI 周知本周安排）。

        节日数据复用 datetime 组件的 festivals 数据层；今天的节日已由
        datetime 组件的 [节日] 行注入，本块只补充明天~本周日，避免重复。
        """
        from ..datetime import festivals

        today = date.today()
        # 本周日（周日当天窗口收缩为只有今天，至少延伸到明天保证「明天」行在场）
        sunday = max(today + timedelta(days=6 - today.weekday()), today + timedelta(days=1))
        events = store.events_between(today, sunday)

        by_day: Dict[str, List[str]] = {}
        for event in events:
            by_day.setdefault(str(event["date"]), []).append(self._event_text(event))
        # 本周后半段节日（明天~周日；今天的节日 datetime 组件已注入）
        day = today + timedelta(days=1)
        while day <= sunday:
            names = festivals.festivals_on(day)
            if names:
                label = "、".join(names) + "（节日）"
                by_day.setdefault(day.isoformat(), []).append(label)
            day += timedelta(days=1)
        if not by_day:
            return None

        tomorrow = today + timedelta(days=1)
        parts: List[str] = []
        week_rest: List[str] = []
        for day_iso in sorted(by_day):
            day = date.fromisoformat(day_iso)
            items = "；".join(by_day[day_iso])
            if day == today:
                parts.append(f"今天：{items}")
            elif day == tomorrow:
                parts.append(f"明天：{items}")
            else:
                week_rest.append(f"{day.strftime('%m-%d')} {items}")
        if week_rest:
            parts.append("本周：" + "；".join(week_rest))
        return "[日程] " + "｜".join(parts)

    def detail(self) -> Dict[str, Any]:
        """面板展示：未来 14 天事件 + 订阅同步状态 + 本地事件总数。"""
        today = date.today()
        upcoming = store.events_between(today, today + timedelta(days=14))
        return {
            "upcoming": upcoming,
            "subscriptions": [
                {"name": sub["name"], "enabled": sub["enabled"],
                 **self._sync_status.get(sub["name"], {})}
                for sub in self.subscriptions()
            ],
            "local_count": sum(
                1 for e in store.load_events()
                if e.get("source", "local") == "local"),
            "sync_minutes": self.get_config("sync_minutes", 60),
        }
