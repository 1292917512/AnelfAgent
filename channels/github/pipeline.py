"""GitHub 事件过滤、持久接收、聚合和可恢复投递。

Model Experience: 事件按仓库或议题送入会话，正文仅使用已选择的字段。
Token effect: 聚合和摘要限制每批消息体积，无额外模型调用。
Cache effect: 只增加会话事件，不修改稳定前缀。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional

from core.log import log

from .config import GitHubConfig
from .events import (
    EventPriority,
    GitHubEvent,
    RawEvent,
    branch_allowed,
    contains_mention,
    event_subscribed,
    mention_text,
    render_event,
)
from .formatting import digest_line, format_event_message
from .journal import EventJournal
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
    """串行处理事件，投递失败时保留持久记录供恢复。"""

    def __init__(self, channel: Any, *, digest: DigestBuffer, stats: EventStats,
                 journal: EventJournal | None = None) -> None:
        self._channel = channel
        self._digest = digest
        self._stats = stats
        self.journal = journal or EventJournal(directory=stats._dir)
        self._buffers: dict[str, list[GitHubEvent]] = {}
        self._flush_tasks: dict[str, asyncio.Task[None]] = {}
        self._active: set[str] = set()
        self._lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._stopping = False
        self.ignored_count = 0
        self.dispatched_count = 0

    @property
    def _config(self) -> GitHubConfig:
        return self._channel.config

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self) -> None:
        """启动持久收件箱的恢复处理。"""
        if self.running:
            return
        self._stopping = False
        await self.journal.prune()
        await self.journal.restore_processing()
        self._worker = asyncio.create_task(self._recover(), name="github-inbox")

    async def accept_raw(self, raw: RawEvent) -> str:
        """仅持久接收，由恢复任务异步处理。"""
        if self._stopping:
            raise RuntimeError("GitHub 频道正在停止")
        sub = self._config.subscription_for(raw.repo_full_name)
        if sub and raw.account and raw.account != sub.account:
            return "ignored"
        _, inserted = await self.journal.accept(raw, self._config.webhook_queue_size)
        return "accepted" if inserted else "duplicate"

    async def submit_raw(self, raw: RawEvent) -> GitHubEvent | None:
        """持久接收并处理一条事件，重复事件不再次投递。"""
        if self._stopping:
            raise RuntimeError("GitHub 频道正在停止")
        key, inserted = await self.journal.accept(raw, self._config.webhook_queue_size)
        if not inserted:
            return None
        return await self._process(key, raw)

    async def _recover(self) -> None:
        """重试未完成记录，保留失败记录和错误原因。"""
        last_prune = time.time()
        while True:
            try:
                if time.time() - last_prune > 3600:
                    await self.journal.prune()
                    last_prune = time.time()
                for key, raw in await self.journal.pending(self._config.webhook_queue_size):
                    if key not in self._active:
                        try:
                            await self._process(key, raw)
                        except Exception as exc:
                            log(f"GitHub: 事件待重试: {exc}", "WARNING", tag="通道")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"GitHub: 收件箱处理失败: {exc}", "ERROR", tag="通道")
            await asyncio.sleep(1)

    async def _process(self, key: str, raw: RawEvent) -> GitHubEvent | None:
        """过滤、渲染和路由持久事件。"""
        async with self._lock:
            if key in self._active or not await self.journal.claim(key):
                return None
            self._active.add(key)
            try:
                event = await self._prepare(raw)
                if event is None:
                    self.ignored_count += 1
                    await self.journal.complete([key])
                    self._active.discard(key)
                    return None
                event.extra["_journal_id"] = key
                sub = self._config.subscription_for(event.repo_full_name)
                if event.priority == EventPriority.DIGEST:
                    self._record(event)
                    if sub is None or sub.digest:
                        self._digest.add(event.repo_full_name, digest_line(event), day=time.strftime("%Y-%m-%d", time.localtime(event.occurred_at)))
                        self._digest.save()
                    await self.journal.complete([key])
                    self._active.discard(key)
                    return event
                bucket = event.repo_full_name
                if sub and sub.session_mode == "subject" and event.extra.get("subject"):
                    bucket += "#" + str(event.extra["subject"])
                self._buffers.setdefault(bucket, []).append(event)
                if event.priority == EventPriority.IMMEDIATE or len(self._buffers[bucket]) >= self._config.aggregate_max_events:
                    await self._flush(bucket)
                else:
                    self._ensure_flush_timer(bucket)
                return event
            except BaseException as exc:
                if key in self._active and not any(key == event.extra.get("_journal_id") for batch in self._buffers.values() for event in batch):
                    self._active.discard(key)
                    if not isinstance(exc, asyncio.CancelledError):
                        await self.journal.failed(key, str(exc))
                raise

    async def _prepare(self, raw: RawEvent) -> GitHubEvent | None:
        """匹配订阅、排除本频道评论并设置优先级。"""
        cfg = self._config
        sub = cfg.subscription_for(raw.repo_full_name)
        if sub is None:
            if not raw.repo_full_name.startswith("@") or not event_subscribed(cfg.account_events, [], raw.name, raw.action):
                return None
        else:
            if raw.account and raw.account != sub.account:
                return None
            if not event_subscribed(sub.events, cfg.default_events, raw.name, raw.action):
                return None
            if raw.name == "push" and not branch_allowed(sub.branches, str(raw.payload.get("ref") or "")):
                return None
        if await self.journal.is_outgoing(raw):
            return None
        event = render_event(raw, body_limit=cfg.body_digest_chars)
        if event is None:
            return None
        if sub:
            event.local_path = sub.local_path
            if sub.delivery != "smart":
                event.priority = EventPriority(sub.delivery)
            elif sub.priority_boost and event.priority == EventPriority.NORMAL:
                event.priority = EventPriority.IMMEDIATE
        if cfg.watch_mentions_of and contains_mention(mention_text(raw), cfg.watch_mentions_of):
            event.mention = True
            event.priority = EventPriority.IMMEDIATE
        quiet = parse_quiet_hours(cfg.quiet_hours)
        if quiet and in_quiet_hours(quiet):
            if event.priority == EventPriority.IMMEDIATE:
                event.priority = EventPriority.NORMAL
            elif event.priority == EventPriority.NORMAL:
                event.priority = EventPriority.DIGEST
        return None if event.priority == EventPriority.IGNORE else event

    def _record(self, event: GitHubEvent) -> None:
        """在事件处理成功后登记统计。"""
        self._stats.record(event.repo_full_name, event.event_key, {
            "id": event.event_id, "ts": event.occurred_at, "repo": event.repo_full_name,
            "name": event.event_name, "action": event.action, "title": event.title,
            "url": event.url, "priority": event.priority.value, "context": {
                key: value for key, value in event.extra.items() if not key.startswith("_")
            },
        }, day=time.strftime("%Y-%m-%d", time.localtime(event.occurred_at)))
        self._stats.save()

    def _ensure_flush_timer(self, bucket: str) -> None:
        """从首条事件开始计算聚合窗口。"""
        task = self._flush_tasks.get(bucket)
        if task is not None and not task.done():
            return

        async def flush_later() -> None:
            try:
                await asyncio.sleep(max(self._config.aggregate_window_sec, 1))
                await self.flush_repo(bucket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"GitHub: 聚合投递待重试: {exc}", "WARNING", tag="通道")

        self._flush_tasks[bucket] = asyncio.create_task(flush_later(), name=f"github-flush-{bucket}")

    async def _flush(self, bucket: str) -> None:
        """逐批派发，失败批次恢复为待处理记录。"""
        task = self._flush_tasks.pop(bucket, None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        events = self._buffers.pop(bucket, [])
        if not events:
            return
        max_events = self._config.aggregate_max_events
        for offset in range(0, len(events), max_events):
            batch = events[offset:offset + max_events]
            keys = [str(event.extra["_journal_id"]) for event in batch]
            repo = batch[0].repo_full_name
            sub = self._config.subscription_for(repo)
            fields = sub.message_fields if sub and sub.message_fields is not None else self._config.message_fields
            text = format_event_message(self._coalesce_push(batch), fields=fields)
            try:
                await self._channel.dispatch_events(repo, batch, text=text)
                await self.journal.complete(keys)
                for event in batch:
                    self._record(event)
                self.dispatched_count += len(batch)
            except BaseException as exc:
                remaining = events[offset:]
                for event in remaining:
                    key = str(event.extra["_journal_id"])
                    self._active.discard(key)
                    if not isinstance(exc, asyncio.CancelledError):
                        await self.journal.failed(key, str(exc))
                raise
            finally:
                self._active.difference_update(keys)

    async def flush_repo(self, repo: str) -> None:
        """冲刷一个聚合会话。"""
        async with self._lock:
            await self._flush(repo)

    async def flush_all(self) -> None:
        """冲刷所有会话，失败事件保留在收件箱。"""
        for bucket in list(self._buffers):
            await self.flush_repo(bucket)

    @staticmethod
    def _coalesce_push(events: List[GitHubEvent]) -> List[GitHubEvent]:
        """同窗同分支、同一已知发起者的 push 合并，保留行为人归属。"""
        result: List[GitHubEvent] = []
        push_slots: Dict[tuple[str, str], GitHubEvent] = {}
        for ev in events:
            if ev.event_name != "push" or not ev.actor:
                result.append(ev)
                continue
            branch = str(ev.extra.get("branch") or "")
            key = (branch, ev.actor)
            slot = push_slots.get(key)
            if slot is None:
                copy = replace(ev, extra=dict(ev.extra))
                push_slots[key] = copy
                result.append(copy)
                continue
            total = int(slot.extra.get("size", 1)) + int(ev.extra.get("size", 1))
            slot.extra["size"] = total
            slot.title = f"push {branch} · {total} 个 commit(聚合)"
            if ev.body_digest:
                slot.body_digest = (slot.body_digest + "\n" + ev.body_digest).strip()
            if ev.url:
                slot.url = ev.url
        return result


    async def stop(self) -> None:
        """停止恢复任务和聚合计时器，尽力投递缓冲记录。"""
        self._stopping = True
        tasks = [task for task in [self._worker, *self._flush_tasks.values()] if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._worker = None
        self._flush_tasks.clear()
        for bucket in list(self._buffers):
            try:
                await self.flush_repo(bucket)
            except Exception as exc:
                log(f"GitHub: 停止时保留待投递事件: {exc}", "WARNING", tag="通道")
        self._active.clear()
        self._digest.save()
        self._stats.save(force=True)
