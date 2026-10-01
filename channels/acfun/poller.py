"""AcFun 通知轮询 — 周期性拉取通知中心，增量派发为入站消息。

AcFun 无实时私信/通知推送（acfunsdk-ws 的 IM reader 为空桩且长期失修），
入站采用轮询：reply/at（对 Bot 的评论回复与提及）始终触发思维；
like/gift/system 按频道配置决定记录或触发。

防重放设计：每类通知的已见键集合持久化（PollCursorStore），
某类首次轮询只播种不派发，重启后也不会把历史通知重新推给思维。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, List, Optional

from acfunsdk.exceptions import NotInCar

from channels._shared.poller import _STOP, BaseNotificationPoller
from core.log import log

from .parser import dedup_key, notification_to_message
from .state import poll_state_path

if TYPE_CHECKING:
    from .adapter import AcfunChannel

class NotificationPoller(BaseNotificationPoller):
    """AcFun 通知中心轮询器（骨架在 channels._shared.poller）。"""

    def __init__(self, channel: "AcfunChannel") -> None:
        super().__init__(channel, channel_name="AcFun", cursor_path=poll_state_path())

    async def _handle_loop_error(self, exc: Exception, interval: int) -> Optional[str]:
        if isinstance(exc, NotInCar):
            log("AcFun: 登录态失效，轮询停止", "WARNING", tag="通道")
            self._channel.on_login_expired()
            return _STOP
        return None

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
            pending_keys = self._cursors.collect_pending(kind, keys)
            if pending_keys is None:
                log(f"AcFun: 通知游标已播种 kind={kind}（{len(keys)} 条历史不派发）", "DEBUG", tag="通道")
                continue
            by_key = dict(zip(keys, items, strict=False))
            for key in pending_keys:
                # 逐条隔离 + 成功才标记已见：单条派发失败不阻塞其余条目，
                # 失败条目保持未见、下轮重派（at-least-once）
                item = by_key[key]
                try:
                    if not self._whitelist_allows(item.get("uid")):
                        self._cursors.mark(kind, key)
                        continue
                    # 点赞通知降噪：未开启触发时仅计数，不写入会话历史
                    if kind == "like" and not cfg.like_trigger_mind:
                        self.like_count += 1
                        self._cursors.mark(kind, key)
                        continue
                    message = notification_to_message(
                        kind, item,
                        like_trigger_mind=cfg.like_trigger_mind,
                        gift_trigger_mind=cfg.gift_trigger_mind,
                    )
                    if message is None:
                        self._cursors.mark(kind, key)
                        continue
                    await self._channel.on_message(message)
                    self._cursors.mark(kind, key)
                    self.dispatch_count += 1
                except Exception as exc:
                    log(f"AcFun: 通知派发失败（下轮重派）kind={kind}: {exc}", "WARNING", tag="通道")
        if kind_failures and kind_failures == len(kinds):
            raise RuntimeError(f"全部通知类别拉取失败: {self.last_error}")
        self._cursors.save()
        self.last_poll_at = time.time()
        if not kind_failures:
            self.last_error = ""
