"""GitHub 轮询器 — 每仓 Events API / releases 增量拉取(默认接收模式,零公网依赖)。

可靠性设计:
- **首次只播种不派发**(PollCursorStore 语义):启动/新增订阅不把历史事件重放给 AI;
- **ETag 条件请求**:304 不占限流配额,长周期轮询的预算根基;
- **游标续传**:已见事件 ID 落盘,重启不重放;
- **失败分级**:401/404 暂停该仓并告警一次(不轰炸)、限流全局暂停至 reset、
  其他异常指数退避,连续 5 次熔断 30 分钟;
- **配额守卫**:剩余配额低于 rate_limit_reserve 时暂停轮询至 reset。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from agent.channel.poll_cursor import PollCursorStore
from core.log import log

from .client import AuthError, GitHubClient, NotFoundError, RateLimitError
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
        self._cursors = PollCursorStore(os.path.join(github_data_dir(), _CURSOR_FILE), channel=_LOG)
        self._repo_state: Dict[str, Dict[str, Any]] = {}
        self._global_paused_until: float = 0.0
        self._dirty = False
        self.last_tick_at: float = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

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
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._save_state()
        self._cursors.save()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

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
        # 配额守卫:剩余配额不足时暂停至 reset(不动用预留配额)
        if 0 <= self._client.rate_remaining < int(cfg.rate_limit_reserve):
            reset = self._client.rate_reset_at
            self._global_paused_until = max(reset, time.time() + 60)
            log(f"{_LOG}: 剩余配额 {self._client.rate_remaining} 低于预留线,暂停轮询至 reset",
                "WARNING", tag="通道")
            return

        due: List[Any] = []
        for sub in cfg.repos:
            state = self._repo_state.setdefault(sub.full_name, {})
            if time.time() < float(state.get("paused_until") or 0):
                continue
            interval = self._interval_for(sub)
            last = float(state.get("last_poll_at") or 0)
            if time.time() - last >= interval:
                due.append(sub)
        if not due:
            return
        await asyncio.gather(*(self._poll_guarded(sub) for sub in due))
        self._save_state()
        self._cursors.save()

    def _interval_for(self, sub: Any) -> float:
        cfg = self._channel.config
        interval = sub.poll_interval_sec or int(cfg.default_poll_interval_sec)
        floor = _ANON_MIN_INTERVAL if cfg.auth_mode == "none" else _MIN_INTERVAL
        return max(float(interval), float(floor))

    async def _poll_guarded(self, sub: Any) -> None:
        async with self._sem:
            try:
                await self._poll_repo(sub)
            except asyncio.CancelledError:
                raise
            except RateLimitError as exc:
                self._global_paused_until = max(
                    time.time() + exc.retry_after, exc.reset_at, time.time() + 60,
                )
                log(f"{_LOG}: 限流,全局暂停 {int(self._global_paused_until - time.time())}s",
                    "WARNING", tag="通道")
            except (AuthError, NotFoundError) as exc:
                # 凭据失效/仓不可见:暂停该仓直到用户介入(改配置即清),告警一次
                state = self._repo_state.setdefault(sub.full_name, {})
                state["paused_until"] = time.time() + 86400 * 365
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
                    # 指数退避:interval * 2^fails(封顶 16 倍)
                    factor = min(2 ** fails, _MAX_BACKOFF_FACTOR)
                    state["last_poll_at"] = (
                        time.time() - self._interval_for(sub) + self._interval_for(sub) * factor
                    )
                    log(f"{_LOG}: {sub.full_name} 轮询异常(连续 {fails} 次): {exc}",
                        "WARNING", tag="通道")
                self._dirty = True

    # ------------------------------------------------------------------
    # 单仓轮询
    # ------------------------------------------------------------------

    async def _poll_repo(self, sub: Any) -> None:
        repo = sub.full_name
        state = self._repo_state.setdefault(repo, {})
        cfg = self._channel.config

        # 1) Events API(综合事件流,ETag 条件请求)
        resp = await self._client.get(
            f"/repos/{repo}/events",
            params={"per_page": 30},
            etag=str(state.get("etag_events") or ""),
        )
        if not resp.not_modified:
            if resp.etag:
                state["etag_events"] = resp.etag
            items = resp.data if isinstance(resp.data, list) else []
            await self._dispatch_new_events(sub, items)
        state["last_poll_at"] = time.time()
        state["fail_count"] = 0
        state["last_error"] = ""
        if state.get("alert_sent"):
            state["alert_sent"] = False  # 恢复后允许下次再告警
        self._dirty = True

        # 2) releases/latest(低频高价值单独盯,仅订阅 release 族时)
        if event_subscribed(sub.events, cfg.default_events, "release", "published"):
            try:
                await self._poll_release(repo, state)
            except NotFoundError:
                pass  # 无 release 的仓常态 404,不影响主事件流

    async def _dispatch_new_events(self, sub: Any, items: List[Dict[str, Any]]) -> None:
        """游标过滤后逐条派发(最旧先发;首次播种不派发)。"""
        repo = sub.full_name
        by_id = {str(x.get("id")): x for x in items if x.get("id")}
        keys = list(by_id.keys())  # API 返回最新在前
        pending = self._cursors.collect_pending("events", keys, seed_key=repo)
        if pending is None:
            log(f"{_LOG}: {repo} 首次轮询完成播种({len(keys)} 条历史不派发)", tag="通道")
            return
        if len(pending) >= 30:
            # events API 单页上限 30,一轮涌出更多说明有缺口
            log(f"{_LOG}: {repo} 单轮新事件达单页上限,可能存在事件缺口", "WARNING", tag="通道")
        for key in pending:
            raw_item = by_id.get(key)
            if raw_item is None:
                self._cursors.mark("events", key)
                continue
            try:
                await self._pipeline.submit_raw(from_poll_api(raw_item))
            except Exception as exc:
                # 派发失败不标已见,下一轮重试该条
                log(f"{_LOG}: 事件派发失败({repo}#{key}): {exc}", "WARNING", tag="通道")
                continue
            self._cursors.mark("events", key)

    async def _poll_release(self, repo: str, state: Dict[str, Any]) -> None:
        resp = await self._client.get(
            f"/repos/{repo}/releases/latest",
            etag=str(state.get("etag_release") or ""),
        )
        if resp.not_modified:
            return
        if resp.etag:
            state["etag_release"] = resp.etag
        rel = resp.data if isinstance(resp.data, dict) else {}
        release_key = f"{rel.get('id')}:{rel.get('published_at')}"
        if not rel.get("id") or state.get("release_key") == release_key:
            return
        first_seen = not state.get("release_key")
        state["release_key"] = release_key
        self._dirty = True
        if first_seen:
            return  # 首次只播种(与事件游标同纪律,不重放历史)
        raw = RawEvent(
            name="release", action="published", repo_full_name=repo,
            actor=str((rel.get("author") or {}).get("login") or ""),
            occurred_at=time.time(),
            payload={"action": "published", "release": rel},
            event_id=f"release:{release_key}",
            source="poll",
        )
        await self._pipeline.submit_raw(raw)

    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        if self._dirty:
            save_json(_POLL_STATE_FILE, {"repos": self._repo_state})
            self._dirty = False

    def repo_states(self) -> Dict[str, Dict[str, Any]]:
        """状态快照(/status 端点与 health_check 用)。"""
        return {repo: dict(state) for repo, state in self._repo_state.items()}
