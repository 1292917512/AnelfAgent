"""AcFun 通知轮询 — 周期性拉取通知中心，增量派发为入站消息。

AcFun 无实时私信/通知推送（acfunsdk-ws 的 IM reader 为空桩且长期失修），
入站采用轮询：reply/at（对 Bot 的评论回复与提及）始终触发思维；
like/gift/system 按频道配置决定记录或触发。

防重放设计：每类通知的已见键集合持久化（PollCursorStore），
某类首次轮询只播种不派发，重启后也不会把历史通知重新推给思维。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from acfunsdk.exceptions import NotInCar

from core.log import log

from .parser import dedup_key, notification_to_message
from .state import PollCursorStore

if TYPE_CHECKING:
    from .adapter import AcfunChannel

_MIN_INTERVAL = 15
_MAX_BACKOFF = 300


class NotificationPoller:
    """AcFun 通知中心轮询器（后台任务，随频道 start/stop 生命周期）。"""

    def __init__(self, channel: "AcfunChannel") -> None:
        self._channel = channel
        self._task: Optional[asyncio.Task] = None
        self._cursors = PollCursorStore()
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
        self._task = asyncio.create_task(self._loop(), name="acfun-notify-poller")

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

    def _enabled_kinds(self) -> List[str]:
        cfg = self._channel.config
        kinds = ["reply", "at"]
        if cfg.notify_like:
            kinds.append("like")
        if cfg.notify_gift:
            kinds.append("gift")
        if cfg.notify_system:
            kinds.extend(["notice", "system"])
        return kinds

    def _whitelist_allows(self, item: Dict[str, Any]) -> bool:
        cfg = self._channel.config
        if not cfg.whitelist_enabled:
            return True
        allowed = {x.strip() for x in cfg.user_whitelist.split(",") if x.strip()}
        return str(item.get("uid") or "") in allowed

    async def _loop(self) -> None:
        failures = 0
        while True:
            interval = max(int(self._channel.config.poll_interval_seconds), _MIN_INTERVAL)
            try:
                await self._poll_once()
                failures = 0
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except NotInCar:
                log("AcFun: 登录态失效，轮询停止", "WARNING", tag="通道")
                self._channel.on_login_expired()
                return
            except Exception as exc:
                failures += 1
                self.last_error = str(exc)
                backoff = min(interval * (2 ** failures), _MAX_BACKOFF)
                log(f"AcFun: 通知轮询异常（{failures} 次连失败，{backoff}s 后重试）: {exc}", "WARNING", tag="通道")
                await asyncio.sleep(backoff)

    async def _poll_once(self) -> None:
        client = self._channel.client
        if not client.is_logined:
            raise NotInCar()
        cfg = self._channel.config
        kinds = self._enabled_kinds()
        kind_failures = 0
        for kind in kinds:
            # 逐类别故障隔离：单一类别解析/网络失败不阻塞其他类别，
            # 仅当全部类别失败（多为断网）才按整轮失败进入退避
            try:
                items = await client.run(client.get_notifications, kind, 1)
            except NotInCar:
                raise
            except Exception as exc:
                kind_failures += 1
                self.last_error = f"{kind}: {exc}"
                log(f"AcFun: 通知拉取失败 kind={kind}: {exc}", "WARNING", tag="通道")
                continue
            keys = [dedup_key(kind, item) for item in items]
            if not self._cursors.is_seeded(kind):
                for key in keys:
                    self._cursors.mark(kind, key)
                self._cursors.mark_seeded(kind)
                log(f"AcFun: 通知游标已播种 kind={kind}（{len(keys)} 条历史不派发）", "DEBUG", tag="通道")
                continue
            # 列表为最新在前，反转为最旧先派发保持时序
            pending = [
                (key, item) for key, item in zip(keys, items, strict=False)
                if not self._cursors.is_seen(kind, key)
            ]
            for key, item in reversed(pending):
                self._cursors.mark(kind, key)
                if not self._whitelist_allows(item):
                    continue
                # 点赞通知降噪：未开启触发时仅计数，不写入会话历史
                if kind == "like" and not cfg.like_trigger_mind:
                    self.like_count += 1
                    continue
                message = notification_to_message(
                    kind, item,
                    like_trigger_mind=cfg.like_trigger_mind,
                    gift_trigger_mind=cfg.gift_trigger_mind,
                )
                if message is None:
                    continue
                await self._channel.on_message(message)
                self.dispatch_count += 1
        if kind_failures and kind_failures == len(kinds):
            raise RuntimeError(f"全部通知类别拉取失败: {self.last_error}")
        self._cursors.save()
        self.last_poll_at = time.time()
        if not kind_failures:
            self.last_error = ""
