"""按仓库增量轮询、分页、配额退避和游标恢复。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from agent.channel.poll_cursor import PollCursorStore
from core.log import log

from .client import AuthError, GitHubClient, NotFoundError, RateLimitError
from .config import RepoSubscription
from .events import RawEvent, event_subscribed, from_poll_api
from .pipeline import EventPipeline
from .state import github_data_dir, load_json, save_json

_LOG = "GitHub"
_TICK_SEC = 15
_MIN_INTERVAL = 60
_ANON_MIN_INTERVAL = 900   # 匿名形态只有 60 req/h,间隔自动抬高
_CIRCUIT_FAILS = 5
_CIRCUIT_COOLDOWN = 1800.0
_MAX_BACKOFF_FACTOR = 16
_POLL_STATE_FILE = "poll_state.json"
_CURSOR_FILE = "poll_cursors.json"


class GitHubPoller:
    """轮询器(挂在频道实例上;单 supervisor 循环 + 每仓独立游标/熔断)。"""

    def __init__(self, channel: Any, client: GitHubClient, pipeline: EventPipeline) -> None:
        self._channel = channel
        self._client = client
        self._pipeline = pipeline
        self._task: Optional[asyncio.Task] = None
        self._sem = asyncio.Semaphore(4)
        self._cursors = PollCursorStore(os.path.join(github_data_dir(), _CURSOR_FILE), channel=_LOG, max_per_kind=1000)
        self._repo_state: Dict[str, Dict[str, Any]] = {}
        self._global_paused_until: float = 0.0
        self._dirty = False
        self.last_tick_at: float = 0.0


    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._repo_state = load_json(_POLL_STATE_FILE, {}).get("repos", {}) or {}
        self._cursors.load()
        self._task = asyncio.create_task(self._loop(), name="github-poller")
        log(f"{_LOG}: 轮询器已启动({len(self._channel.config.repos)} 个订阅仓)", tag="通道")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                log(f"{_LOG}: 轮询任务已取消", "DEBUG", tag="通道")
            except Exception as exc:
                log(f"{_LOG}: 轮询任务停止时异常: {exc}", "DEBUG", tag="通道")
            self._task = None
        self._cursors.save()
        self._save_state()


    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"{_LOG}: 轮询循环异常(15s 后继续): {exc}", "WARNING", tag="通道")
            await asyncio.sleep(_TICK_SEC)

    async def _tick(self) -> None:
        self.last_tick_at = time.time()
        cfg = self._channel.config
        if time.time() < self._global_paused_until:
            return
        due: List[Any] = []
        for sub in cfg.repos:
            state = self._repo_state.setdefault(sub.full_name, {})
            if time.time() < float(state.get("paused_until") or 0):
                continue
            path = f"/repos/{sub.full_name}/events"
            limit, remaining, reset = self._client.quota_for(path)
            reserve = min(cfg.rate_limit_reserve, max(1, limit // 10))
            if 0 <= remaining < reserve and reset > time.time():
                state["quota_paused_until"] = reset
                continue
            state.pop("quota_paused_until", None)
            interval = self._interval_for(sub)
            last = float(state.get("last_poll_at") or 0)
            if time.time() - last >= interval:
                due.append(sub)
        if not due:
            return
        await asyncio.gather(*(self._poll_guarded(sub) for sub in due))
        self._cursors.save()
        self._save_state()

    def _interval_for(self, sub: RepoSubscription) -> float:
        cfg = self._channel.config
        interval = sub.poll_interval_sec or int(cfg.default_poll_interval_sec)
        anonymous = self._client.auth.anonymous(f"/repos/{sub.full_name}/events") if self._client.auth else cfg.auth_mode == "none"
        floor = _ANON_MIN_INTERVAL if anonymous else _MIN_INTERVAL
        return max(float(interval), float(floor), float(self._repo_state.get(sub.full_name, {}).get("server_interval", 0)))

    async def _poll_guarded(self, sub: RepoSubscription) -> None:
        async with self._sem:
            try:
                await self._poll_repo(sub)
            except asyncio.CancelledError:
                raise
            except RateLimitError as exc:
                state = self._repo_state.setdefault(sub.full_name, {})
                state["paused_until"] = max(
                    time.time() + exc.retry_after, exc.reset_at, time.time() + 60,
                )
                log(f"{_LOG}: 限流,仓库暂停 {int(state['paused_until'] - time.time())}s",
                    "WARNING", tag="通道")
            except (AuthError, NotFoundError) as exc:
                state = self._repo_state.setdefault(sub.full_name, {})
                state["paused_until"] = time.time() + _CIRCUIT_COOLDOWN
                state["last_error"] = str(exc)
                self._dirty = True
                if not state.get("alert_sent"):
                    state["alert_sent"] = True
                    await self._channel.alert_repo(
                        sub.full_name,
                        f"轮询已暂停: {exc}。请检查 token 权限/仓库名,修复后经配置页或工具恢复。",
                    )
            except Exception as exc:
                state = self._repo_state.setdefault(sub.full_name, {})
                fails = int(state.get("fail_count") or 0) + 1
                state["fail_count"] = fails
                state["last_error"] = str(exc)
                if fails >= _CIRCUIT_FAILS:
                    state["paused_until"] = time.time() + _CIRCUIT_COOLDOWN
                    state["fail_count"] = 0
                    log(f"{_LOG}: {sub.full_name} 连续失败熔断 30 分钟: {exc}", "WARNING", tag="通道")
                else:
                    factor = min(2 ** fails, _MAX_BACKOFF_FACTOR)
                    state["last_poll_at"] = (
                        time.time() - self._interval_for(sub) + self._interval_for(sub) * factor
                    )
                    log(f"{_LOG}: {sub.full_name} 轮询异常(连续 {fails} 次): {exc}",
                        "WARNING", tag="通道")
                self._dirty = True


    async def _poll_repo(self, sub: RepoSubscription) -> None:
        repo = sub.full_name
        state = self._repo_state.setdefault(repo, {})
        cfg = self._channel.config

        resp = await self._client.get(
            f"/repos/{repo}/events",
            params={"per_page": 100},
            etag=str(state.get("etag_events") or ""),
        )
        if not resp.not_modified:
            items = resp.data if isinstance(resp.data, list) else []
            page = 1
            next_page = 'rel="next"' in resp.headers.get("link", "")
            while (next_page and page < cfg.poll_max_pages and self._cursors.is_seeded(repo)
                   and not any(self._cursors.is_seen(f"events:{repo.lower()}", str(item.get("id"))) for item in items)):
                page += 1
                response = await self._client.get(f"/repos/{repo}/events", params={"per_page": 100, "page": page})
                items.extend(response.data if isinstance(response.data, list) else [])
                next_page = 'rel="next"' in response.headers.get("link", "")
            state["possible_gap"] = bool(next_page and page >= cfg.poll_max_pages and not any(
                self._cursors.is_seen(f"events:{repo.lower()}", str(item.get("id"))) for item in items))
            await self._dispatch_new_events(sub, items)
            if resp.etag:
                state["etag_events"] = resp.etag
        interval_header = resp.headers.get("x-poll-interval", "")
        if interval_header.isdigit():
            state["server_interval"] = int(interval_header)
        state["last_poll_at"] = time.time()
        state["fail_count"] = 0
        state["last_error"] = ""
        if state.get("alert_sent"):
            state["alert_sent"] = False  # 恢复后允许下次再告警
        self._dirty = True

        if event_subscribed(sub.events, cfg.default_events, "release", "published"):
            try:
                await self._poll_release(repo, state)
            except NotFoundError:
                log(f"{_LOG}: {repo} 无可见 release", "DEBUG", tag="通道")

    async def _dispatch_new_events(self, sub: RepoSubscription, items: List[Dict[str, Any]]) -> None:
        """游标过滤后逐条派发(最旧先发;首次播种不派发)。"""
        repo = sub.full_name
        by_id = {str(x.get("id")): x for x in items if x.get("id")}
        keys = list(by_id.keys())  # API 返回最新在前
        kind = f"events:{repo.lower()}"
        for key in keys:
            if self._cursors.is_seen("events", key):
                self._cursors.mark(kind, key)
        pending = self._cursors.collect_pending(kind, keys, seed_key=repo)
        if pending is None:
            log(f"{_LOG}: {repo} 首次轮询完成播种({len(keys)} 条历史不派发)", tag="通道")
            return
        for key in pending:
            raw_item = by_id.get(key)
            if raw_item is None:
                self._cursors.mark(kind, key)
                continue
            raw = from_poll_api(raw_item)
            raw.account = sub.account
            await self._pipeline.submit_raw(raw)
            self._cursors.mark(kind, key)

    async def _poll_release(self, repo: str, state: Dict[str, Any]) -> None:
        resp = await self._client.get(
            f"/repos/{repo}/releases/latest",
            etag=str(state.get("etag_release") or ""),
        )
        if resp.not_modified:
            return
        rel = resp.data if isinstance(resp.data, dict) else {}
        release_key = f"{rel.get('id')}:{rel.get('published_at')}"
        if not rel.get("id") or state.get("release_key") == release_key:
            return
        first_seen = not state.get("release_key")
        if first_seen:
            state["release_key"] = release_key
            state["etag_release"] = resp.etag
            self._dirty = True
            return  # 首次只播种(与事件游标同纪律,不重放历史)
        raw = RawEvent(
            name="release", action="published", repo_full_name=repo,
            actor=str((rel.get("author") or {}).get("login") or ""),
            occurred_at=time.time(),
            payload={"action": "published", "release": rel},
            event_id=f"release:{release_key}",
            source="poll",
        )
        sub = self._channel.config.subscription_for(repo)
        raw.account = sub.account if sub else ""
        await self._pipeline.submit_raw(raw)
        state["release_key"] = release_key
        state["etag_release"] = resp.etag
        self._dirty = True

    def resume(self, repo: str = "") -> None:
        """清除仓库暂停和失败状态，保留已确认游标。"""
        for name, state in self._repo_state.items():
            if not repo or repo.lower() == name.lower():
                for key in ("paused_until", "quota_paused_until", "last_error", "fail_count", "alert_sent", "last_poll_at"):
                    state.pop(key, None)
        self._global_paused_until = 0
        self._dirty = True
        self._save_state()


    def _save_state(self) -> None:
        if self._dirty:
            save_json(_POLL_STATE_FILE, {"repos": self._repo_state})
            self._dirty = False

    def repo_states(self) -> Dict[str, Dict[str, Any]]:
        """状态快照(/status 端点与 health_check 用)。"""
        return {repo: dict(state) for repo, state in self._repo_state.items()}
