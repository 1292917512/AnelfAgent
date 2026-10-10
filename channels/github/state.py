"""GitHub 频道状态 — 数据目录 JSON 持久化(原子写,随 ANELF_DATA_DIR 搬迁)。

文件布局(``<data_dir>/channels/github/``):
- ``poll_state.json``   每仓轮询游标(etag/最近事件 ID/熔断/暂停)
- ``deliveries.json``   webhook delivery id 去重环(FIFO 2000)
- ``digest_buffer.json`` 待汇总的 DIGEST 级事件(每日 digest 任务消费)
- ``stats.json``        每仓每日事件计数(30 天滚动)+ 最近事件环(200 条)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from channels._shared.state import channel_data_dir
from core.file_utils import atomic_write_text
from core.log import log

_LOG = "GitHub"
_DELIVERY_RING_MAX = 2000
_RECENT_EVENTS_MAX = 200
_DIGEST_ROWS_MAX = 300
_STATS_KEEP_DAYS = 30


def github_data_dir() -> str:
    """GitHub 频道数据目录(不存在则创建)。"""
    return channel_data_dir("github")


def load_json(name: str, default: Any, *, directory: str = "") -> Any:
    """容错读 JSON(缺失/损坏返回 default 的副本)。directory 为空用频道数据目录。"""
    path = os.path.join(directory or github_data_dir(), name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log(f"{_LOG}: 状态文件解析失败 ({name}),按默认值重建: {exc}", "WARNING", tag="通道")
        return default


def save_json(name: str, data: Any, *, directory: str = "") -> None:
    """原子写 JSON(缩进格式,崩溃不留半截文件)。directory 为空用频道数据目录。"""
    from pathlib import Path
    path = Path(os.path.join(directory or github_data_dir(), name))
    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    except OSError as exc:
        log(f"{_LOG}: 状态文件保存失败 ({name}): {exc}", "WARNING", tag="通道")


# ----------------------------------------------------------------------
# webhook delivery 去重环
# ----------------------------------------------------------------------


class DeliveryDedup:
    """x-github-delivery 去重环:GitHub 超时重投同一 delivery 时幂等命中。"""

    _FILE = "deliveries.json"

    def __init__(self, *, directory: str = "") -> None:
        self._dir = directory
        self._ids: List[str] = []
        self._set: set[str] = set()

    def load(self) -> None:
        data = load_json(self._FILE, {}, directory=self._dir)
        ids = data.get("ids") if isinstance(data, dict) else None
        if isinstance(ids, list):
            self._ids = [str(x) for x in ids][-_DELIVERY_RING_MAX:]
            self._set = set(self._ids)

    def save(self) -> None:
        save_json(self._FILE, {"ids": self._ids}, directory=self._dir)

    def seen_or_add(self, delivery_id: str) -> bool:
        """已见过返回 True(重复投递);否则登记并返回 False。"""
        if not delivery_id:
            return False
        if delivery_id in self._set:
            return True
        self._ids.append(delivery_id)
        self._set.add(delivery_id)
        if len(self._ids) > _DELIVERY_RING_MAX:
            old = self._ids[: len(self._ids) - _DELIVERY_RING_MAX]
            del self._ids[: len(old)]
            for x in old:
                self._set.discard(x)
        return False


# ----------------------------------------------------------------------
# DIGEST 缓冲与统计
# ----------------------------------------------------------------------


class DigestBuffer:
    """DIGEST 级事件的待汇总缓冲(每日 digest 任务读取;保留今昨两天)。"""

    _FILE = "digest_buffer.json"

    def __init__(self, *, directory: str = "") -> None:
        self._dir = directory
        # {date: {repo: [摘要行...]}}
        self._rows: Dict[str, Dict[str, List[str]]] = {}
        self._dirty = False

    def load(self) -> None:
        data = load_json(self._FILE, {}, directory=self._dir)
        rows = data.get("rows") if isinstance(data, dict) else None
        if isinstance(rows, dict):
            self._rows = {
                str(d): {str(r): [str(x) for x in v] for r, v in repos.items() if isinstance(v, list)}
                for d, repos in rows.items() if isinstance(repos, dict)
            }

    def save(self) -> None:
        if self._dirty:
            save_json(self._FILE, {"rows": self._rows}, directory=self._dir)
            self._dirty = False

    def add(self, repo: str, line: str, *, day: str) -> None:
        day_rows = self._rows.setdefault(day, {})
        rows = day_rows.setdefault(repo, [])
        rows.append(line)
        if len(rows) > _DIGEST_ROWS_MAX:
            del rows[: len(rows) - _DIGEST_ROWS_MAX]
        self._expire(day)
        self._dirty = True

    def snapshot(self, *, days: int = 2) -> Dict[str, Dict[str, List[str]]]:
        """最近 N 天的缓冲快照(不清空,AI 可重复读取)。"""
        keep = _recent_days(days)
        return {d: dict(repos) for d, repos in self._rows.items() if d in keep}

    def _expire(self, today: str) -> None:
        keep = set(_recent_days(2, today=today))
        for d in [d for d in self._rows if d not in keep]:
            del self._rows[d]


class EventStats:
    """事件统计:每日计数(30 天滚动)+ 最近事件环(面板/工具查询用)。"""

    _FILE = "stats.json"

    def __init__(self, *, directory: str = "") -> None:
        self._dir = directory
        # counts: {date: {repo: {event_key: n}}};recent: [事件摘要...]
        self._counts: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._recent: List[Dict[str, Any]] = []
        self._dirty = False
        self._since_add = 0

    def load(self) -> None:
        data = load_json(self._FILE, {}, directory=self._dir)
        if isinstance(data, dict):
            counts = data.get("counts")
            if isinstance(counts, dict):
                self._counts = counts
            recent = data.get("recent")
            if isinstance(recent, list):
                self._recent = [x for x in recent if isinstance(x, dict)][-_RECENT_EVENTS_MAX:]

    def save(self, *, force: bool = False) -> None:
        """落盘(每 10 次新增或强制时才写,防 star 洪峰刷盘)。"""
        if not self._dirty:
            return
        if not force and self._since_add < 10:
            return
        save_json(self._FILE, {"counts": self._counts, "recent": self._recent},
                  directory=self._dir)
        self._dirty = False
        self._since_add = 0

    def record(self, repo: str, event_key: str, summary: Dict[str, Any], *, day: str) -> None:
        repo_counts = self._counts.setdefault(day, {}).setdefault(repo, {})
        repo_counts[event_key] = repo_counts.get(event_key, 0) + 1
        self._expire_counts()
        self._recent.append(summary)
        if len(self._recent) > _RECENT_EVENTS_MAX:
            del self._recent[: len(self._recent) - _RECENT_EVENTS_MAX]
        self._dirty = True
        self._since_add += 1

    def today_counts(self, *, day: str) -> Dict[str, Dict[str, int]]:
        return dict(self._counts.get(day, {}))

    def recent_events(self, repo: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """最近事件环(新→旧;repo 非空时按仓过滤)。"""
        items = self._recent
        if repo:
            target = repo.lower()
            items = [x for x in items if str(x.get("repo", "")).lower() == target]
        return list(reversed(items))[:limit]

    def _expire_counts(self) -> None:
        keep = set(_recent_days(_STATS_KEEP_DAYS))
        for d in [d for d in self._counts if d not in keep]:
            del self._counts[d]


def _recent_days(n: int, *, today: Optional[str] = None) -> List[str]:
    """最近 n 天的日期串列表(含今天,新→旧)。"""
    base = time.strptime(today, "%Y-%m-%d") if today else time.localtime()
    base_ts = time.mktime(base)
    return [
        time.strftime("%Y-%m-%d", time.localtime(base_ts - 86400 * i))
        for i in range(n)
    ]
