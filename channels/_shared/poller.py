"""通知轮询器公共骨架 — 生命周期 / 白名单 / 指数退避循环（模板方法）。

子类实现 ``_poll_once``；差异点经钩子注入：
- ``_next_interval``：本轮间隔（可叠加抖动等策略）；
- ``_on_loop_success``：成功后的状态复位（如凭据错误计数清零）；
- ``_handle_loop_error``：特殊错误分类（登录失效终止、凭据抖动降速等），
  返回 ``_STOP`` 终止循环，返回 ``_HANDLED`` 表示已自处理（继续），
  返回 None 走默认退避。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from agent.channel.poll_cursor import PollCursorStore
from core.log import log

_STOP = "_STOP"
_HANDLED = "_HANDLED"


class BaseNotificationPoller:
    """轮询器基类（后台任务，随频道 start/stop 生命周期）。"""

    _MIN_INTERVAL = 15
    _MAX_BACKOFF = 300

    def __init__(self, channel: Any, *, channel_name: str, cursor_path: str) -> None:
        self._channel = channel
        self._log_name = channel_name
        self._task: Optional[asyncio.Task] = None
        self._cursors = PollCursorStore(cursor_path, channel=channel_name)
        self.last_poll_at: float = 0.0
        self.last_error: str = ""
        self.dispatch_count: int = 0
        self.like_count: int = 0  # 点赞通知只计数（不进历史，防噪音刷屏）

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._cursors.load()
        self._task = asyncio.create_task(
            self._loop(), name=f"{self._channel.channel_id}-poller",
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    # ------------------------------------------------------------------
    # 子类实现 / 钩子
    # ------------------------------------------------------------------

    async def _poll_once(self) -> None:
        """单轮拉取与派发（子类实现）。"""
        raise NotImplementedError

    def _next_interval(self) -> int:
        """本轮轮询间隔（默认取频道配置，夹下限）。"""
        return max(int(self._channel.config.poll_interval_seconds), self._MIN_INTERVAL)

    def _on_loop_success(self) -> None:
        """一轮成功后的钩子（默认无操作）。"""

    async def _handle_loop_error(self, exc: Exception, interval: int) -> Optional[str]:
        """特殊错误分类钩子：_STOP 终止 / _HANDLED 已自处理 / None 走默认退避。"""
        return None

    # ------------------------------------------------------------------

    def _whitelist_allows(self, uid: Any) -> bool:
        """用户白名单判定（未启用时放行）。"""
        cfg = self._channel.config
        if not cfg.whitelist_enabled:
            return True
        allowed = {x.strip() for x in cfg.user_whitelist.split(",") if x.strip()}
        return str(uid or "") in allowed

    async def _loop(self) -> None:
        failures = 0
        while True:
            interval = self._next_interval()
            try:
                await self._poll_once()
                failures = 0
                self._on_loop_success()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                action = await self._handle_loop_error(exc, interval)
                if action == _STOP:
                    return
                if action == _HANDLED:
                    continue
                failures += 1
                self.last_error = str(exc)
                backoff = min(interval * (2 ** failures), self._MAX_BACKOFF)
                log(f"{self._log_name}: 轮询异常（连续 {failures} 次，{backoff}s 后重试）: {exc}",
                    "WARNING", tag="通道")
                await asyncio.sleep(backoff)
