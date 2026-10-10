"""GitHub 事件管线 — 过滤 / 分级 / 聚合防抖 / digest 缓冲 → 注入 AI。

职责链(轮询与 webhook 两模在此汇合,下游对来源无感):
  RawEvent → 订阅匹配 → 分支过滤 → 渲染(render_event)→ 优先级调整
  (priority_boost / @提及升级 / 安静时段降级)→ 统计落账 → 分档路由:
    IMMEDIATE → 先冲刷该仓防抖缓冲(保时序)再立即派发
    NORMAL    → 进 per-repo 防抖窗(窗到期或满额时聚合为一条派发)
    DIGEST    → 只进 digest 缓冲与计数(不写历史,防噪音;每日 digest 任务读取)

防打扰设计:同仓同窗多条事件合并为一条消息;push 事件同分支合并提交数;
洪峰由 aggregate_max_events 拆条;安静时段整体降级。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from core.log import log

from .config import GitHubConfig, RepoSubscription
from .events import (
    EventPriority,
    GitHubEvent,
    RawEvent,
    base_priority,
    branch_allowed,
    contains_mention,
    digest_line,
    event_subscribed,
    format_event_message,
    render_event,
)
from .state import DigestBuffer, EventStats

_LOG = "GitHub"


def parse_quiet_hours(spec: str) -> Optional[tuple[int, int]]:
    """解析 '23:00-08:00' 安静时段(分钟数对;支持跨零点)。非法/空返回 None。"""
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        start_s, end_s = spec.split("-", 1)
        sh, sm = start_s.strip().split(":")
        eh, em = end_s.strip().split(":")
        return (int(sh) * 60 + int(sm), int(eh) * 60 + int(em))
    except (ValueError, AttributeError):
        log(f"{_LOG}: quiet_hours 格式非法(应为 'HH:MM-HH:MM'): {spec}", "WARNING", tag="通道")
        return None


def in_quiet_hours(parsed: tuple[int, int], ts: Optional[float] = None) -> bool:
    """判定 ts(默认当前)是否落在安静时段(跨零点正确环绕)。"""
    start, end = parsed
    if start == end:
        return False
    now = time.localtime(ts if ts is not None else time.time())
    cur = now.tm_hour * 60 + now.tm_min
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


class EventPipeline:
    """事件管线(挂在频道实例上,随 start/stop 生命周期)。"""

    def __init__(self, channel: Any, *, digest: DigestBuffer, stats: EventStats) -> None:
        self._channel = channel
        self._digest = digest
        self._stats = stats
        self._buffers: Dict[str, List[GitHubEvent]] = {}
        self._flush_tasks: Dict[str, asyncio.Task] = {}
        self.ignored_count = 0
        self.dispatched_count = 0

    @property
    def _config(self) -> GitHubConfig:
        return self._channel.config

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def submit_raw(self, raw: RawEvent) -> Optional[GitHubEvent]:
        """提交一条原始事件(过滤→渲染→分级→路由);被丢弃返回 None。"""
        cfg = self._config
        if not raw.repo_full_name:
            self.ignored_count += 1
            return None
        sub = cfg.subscription_for(raw.repo_full_name)
        if sub is None:
            self.ignored_count += 1
            return None
        if not event_subscribed(sub.events, cfg.default_events, raw.name, raw.action):
            self.ignored_count += 1
            return None
        if raw.name == "push":
            ref = str(raw.payload.get("ref") or "")
            if not branch_allowed(sub.branches, ref):
                self.ignored_count += 1
                return None

        event = render_event(raw, body_limit=int(cfg.body_digest_chars))
        if event is None:
            self.ignored_count += 1
            return None

        event.local_path = sub.local_path
        self._adjust_priority(event, sub)
        if event.priority == EventPriority.IGNORE:
            self.ignored_count += 1
            return None

        day = time.strftime("%Y-%m-%d", time.localtime(event.occurred_at))
        self._stats.record(event.repo_full_name, event.event_key, {
            "ts": event.occurred_at, "repo": event.repo_full_name,
            "name": event.event_name, "action": event.action,
            "title": event.title, "url": event.url, "priority": event.priority.value,
        }, day=day)
        self._stats.save()

        if event.priority == EventPriority.DIGEST:
            self._digest.add(event.repo_full_name, digest_line(event), day=day)
            self._digest.save()
            return event
        if event.priority == EventPriority.IMMEDIATE:
            pending = self._pop_buffer(event.repo_full_name)
            events = pending + [event]
            await self._dispatch(event.repo_full_name, events)
            return event
        # NORMAL:进防抖窗
        self._buffers.setdefault(event.repo_full_name, []).append(event)
        if len(self._buffers[event.repo_full_name]) >= int(cfg.aggregate_max_events):
            await self.flush_repo(event.repo_full_name)
        else:
            self._ensure_flush_timer(event.repo_full_name)
        return event

    # ------------------------------------------------------------------
    # 优先级调整(boost / 提及 / 安静时段)
    # ------------------------------------------------------------------

    def _adjust_priority(self, event: GitHubEvent, sub: RepoSubscription) -> None:
        cfg = self._config
        # 数据相关:评论/帖子正文 @提及主人 → IMMEDIATE
        watch_login = cfg.watch_mentions_of.strip()
        if watch_login and event.priority in (EventPriority.NORMAL, EventPriority.DIGEST):
            if contains_mention(event.body_digest, watch_login) or contains_mention(event.title, watch_login):
                event.mention = True
                event.priority = EventPriority.IMMEDIATE
        # 重点仓:NORMAL → IMMEDIATE
        if sub.priority_boost and event.priority == EventPriority.NORMAL:
            event.priority = EventPriority.IMMEDIATE
        # 安静时段整体降级
        quiet = parse_quiet_hours(cfg.quiet_hours)
        if quiet and in_quiet_hours(quiet, time.time()):
            if event.priority == EventPriority.IMMEDIATE:
                event.priority = EventPriority.NORMAL
            elif event.priority == EventPriority.NORMAL:
                event.priority = EventPriority.DIGEST
        event.priority = event.priority or base_priority(event.event_name, event.action)

    # ------------------------------------------------------------------
    # 防抖聚合
    # ------------------------------------------------------------------

    def _ensure_flush_timer(self, repo: str) -> None:
        task = self._flush_tasks.get(repo)
        if task is not None and not task.done():
            return  # 窗口从首条事件起算,新事件不推迟(防滚动聚合永不发送)

        async def _flush_later() -> None:
            try:
                await asyncio.sleep(max(int(self._config.aggregate_window_sec), 1))
                await self.flush_repo(repo)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"{_LOG}: 聚合冲刷异常 ({repo}): {exc}", "WARNING", tag="通道")

        self._flush_tasks[repo] = asyncio.create_task(
            _flush_later(), name=f"github-flush-{repo}",
        )

    def _pop_buffer(self, repo: str) -> List[GitHubEvent]:
        task = self._flush_tasks.pop(repo, None)
        if task is not None and not task.done():
            task.cancel()
        return self._buffers.pop(repo, [])

    async def flush_repo(self, repo: str) -> None:
        """冲刷指定仓的防抖缓冲(push 同分支合并)。"""
        events = self._pop_buffer(repo)
        if not events:
            return
        await self._dispatch(repo, self._coalesce_push(events))

    async def flush_all(self) -> None:
        """频道停止时冲刷全部缓冲(不丢已攒事件)。"""
        for repo in list(self._buffers):
            await self.flush_repo(repo)

    @staticmethod
    def _coalesce_push(events: List[GitHubEvent]) -> List[GitHubEvent]:
        """同窗同分支的 push 事件合并为一条(commit 数累加,正文合并)。"""
        result: List[GitHubEvent] = []
        push_slots: Dict[str, GitHubEvent] = {}
        for ev in events:
            if ev.event_name != "push":
                result.append(ev)
                continue
            branch = str(ev.extra.get("branch") or "")
            slot = push_slots.get(branch)
            if slot is None:
                push_slots[branch] = ev
                result.append(ev)
                continue
            total = int(slot.extra.get("size", 1)) + int(ev.extra.get("size", 1))
            slot.extra["size"] = total
            slot.title = f"push {branch} · {total} 个 commit(聚合)"
            if ev.body_digest:
                slot.body_digest = (slot.body_digest + "\n" + ev.body_digest).strip()
            if ev.url:
                slot.url = ev.url
        return result

    # ------------------------------------------------------------------
    # 派发
    # ------------------------------------------------------------------

    async def _dispatch(self, repo: str, events: List[GitHubEvent]) -> None:
        """渲染消息文本并经频道入站链推送(聚合超上限拆条)。"""
        max_events = max(int(self._config.aggregate_max_events), 1)
        for i in range(0, len(events), max_events):
            batch = events[i: i + max_events]
            text = format_event_message(batch)
            if not text:
                continue
            self.dispatched_count += len(batch)
            await self._channel.dispatch_events(repo, batch, text=text)

    async def stop(self) -> None:
        """停止:取消定时器并冲刷残余缓冲。"""
        for task in self._flush_tasks.values():
            if not task.done():
                task.cancel()
        self._flush_tasks.clear()
        await self.flush_all()
        self._digest.save()
        self._stats.save(force=True)
