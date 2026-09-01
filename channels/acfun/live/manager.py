"""AcFun 直播会话管理器 — 直播模式总开关、观察房间表与事件分级路由。

入站分级（防弹幕洪泛污染历史：每条派发消息即使不触发思维也产生一次历史写）：
- 普通弹幕/点赞/进入 → 仅进内存环形缓冲（上下文注入的数据源）与计数
- 点名弹幕（含 bot 名/uid/自定义触发词）→ 完整派发 trigger_mind=True（弹幕回复闭环）
- 礼物/投蕉/关注/入团 → 完整派发，按 live_gift_trigger_mind 决定是否触发思维
- 连接状态变更 → 系统消息派发进房间会话（重连耗尽/被踢/违规触发思维，其余仅记录）
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional

from core.log import log

from . import signals as live_signals
from .connection import LiveRoomConnection, RoomState

if TYPE_CHECKING:
    from channels.acfun.adapter import AcfunChannel

_CHATTER_WINDOW_SECONDS = 300.0
_MAX_SYSTEM_STATE_EVENTS = 50


class LiveSessionManager:
    """直播模式管理器（由频道实例持有，start/stop 随频道生命周期）。"""

    def __init__(self, channel: "AcfunChannel") -> None:
        self._channel = channel
        self._rooms: Dict[str, LiveRoomConnection] = {}
        self._danmaku: Dict[str, Deque[Dict[str, Any]]] = {}
        self._gift_feed: Dict[str, Deque[str]] = {}
        self._gift_names: Dict[str, Dict[str, str]] = {}
        self._watched: List[str] = []
        self._mode = False
        self._state_events: Deque[str] = deque(maxlen=_MAX_SYSTEM_STATE_EVENTS)

    # ------------------------------------------------------------------
    # 模式与观察列表
    # ------------------------------------------------------------------

    @property
    def mode_enabled(self) -> bool:
        return self._mode

    @property
    def watched(self) -> List[str]:
        return list(self._watched)

    async def set_mode(self, enabled: bool) -> str:
        """开关直播模式：开 = 按 watch 列表建连；关 = 断开全部并清空缓冲。"""
        if enabled == self._mode:
            return "直播模式已是该状态"
        self._mode = enabled
        if enabled:
            cfg_rooms = self._parse_watch_config()
            for uid in cfg_rooms:
                if uid not in self._watched:
                    self._watched.append(uid)
            for uid in list(self._watched):
                await self._connect_room(uid)
            log(f"AcFun直播: 模式开启，观察 {len(self._rooms)} 个房间", tag="通道")
            return f"直播模式已开启（{len(self._rooms)} 个房间连接中）"
        await self._disconnect_all()
        self._danmaku.clear()
        self._gift_feed.clear()
        log("AcFun直播: 模式关闭，已断开全部连接并停止上下文注入", tag="通道")
        return "直播模式已关闭（连接已断开，上下文状态注入已停止）"

    async def watch(self, uid: str) -> str:
        uid = str(uid).strip()
        if not uid.isdigit():
            return "无效的主播 UID"
        max_rooms = max(int(self._channel.config.live_max_rooms), 1)
        if uid not in self._watched:
            if len(self._watched) >= max_rooms:
                return f"观察房间已达上限 {max_rooms}（live_max_rooms 可调），请先 unwatch"
            self._watched.append(uid)
        if self._mode:
            await self._connect_room(uid)
        return f"已观察直播间 live:{uid}" + ("（直播模式未开启，仅记录观察列表）" if not self._mode else "，连接建立中")

    async def unwatch(self, uid: str) -> str:
        uid = str(uid).strip()
        if uid in self._watched:
            self._watched.remove(uid)
        await self._disconnect_room(uid)
        self._danmaku.pop(uid, None)
        self._gift_feed.pop(uid, None)
        return f"已取消观察 live:{uid}（若直播模式仍开启，其余房间不受影响）"

    async def sync_rooms(self) -> None:
        """按配置 watch 列表 diff 同步连接（新增建连，移除断开）；模式未开时仅同步列表。"""
        want = self._parse_watch_config()
        max_rooms = max(int(self._channel.config.live_max_rooms), 1)
        for uid in want[:max_rooms]:
            if uid not in self._watched:
                self._watched.append(uid)
        for uid in list(self._watched):
            if uid not in want:
                self._watched.remove(uid)
                await self._disconnect_room(uid)
                self._danmaku.pop(uid, None)
                self._gift_feed.pop(uid, None)
        if self._mode:
            for uid in self._watched:
                await self._connect_room(uid)

    # ------------------------------------------------------------------
    # 房间连接管理
    # ------------------------------------------------------------------

    def _parse_watch_config(self) -> List[str]:
        raw = str(getattr(self._channel.config, "live_watch_rooms", "") or "")
        return [x.strip() for x in raw.split(",") if x.strip().isdigit()]

    async def _connect_room(self, uid: str) -> None:
        if uid in self._rooms and self._rooms[uid].running:
            return
        conn = LiveRoomConnection(
            uid,
            enter_params_fn=self._channel.live_enter_params_async,
            credentials_fn=self._channel.client.live_ws_credentials,
            on_event=self._handle_event,
            on_state=self._handle_state,
            closed_retry_seconds=float(self._channel.config.live_closed_retry_seconds),
        )
        self._rooms[uid] = conn
        self._danmaku.setdefault(uid, deque(maxlen=self._window_size()))
        self._gift_feed.setdefault(uid, deque(maxlen=10))
        conn.start()

    async def _disconnect_room(self, uid: str) -> None:
        conn = self._rooms.pop(uid, None)
        if conn is not None:
            await conn.stop()

    async def _disconnect_all(self) -> None:
        for uid in list(self._rooms):
            await self._disconnect_room(uid)

    async def stop(self) -> None:
        """频道关停：断开全部（模式状态保留，重启后按配置恢复）。"""
        self._mode = False
        await self._disconnect_all()

    def _window_size(self) -> int:
        return max(int(self._channel.config.live_recent_window), 5)

    # ------------------------------------------------------------------
    # 事件路由（连接回调，全部在事件循环内）
    # ------------------------------------------------------------------

    def _mention_tokens(self) -> List[str]:
        tokens = []
        bot_name = self._channel.client.username
        bot_uid = self._channel.client.uid
        if bot_name and len(bot_name) >= 2:
            tokens.append(bot_name)
        if bot_uid:
            tokens.append(f"@{bot_uid}")
        extra = str(self._channel.config.live_mention_names or "")
        tokens.extend(x.strip() for x in extra.split(",") if len(x.strip()) >= 2)
        return tokens

    def _is_mention(self, text: str) -> bool:
        if not self._channel.config.live_mention_trigger:
            return False
        return any(token in text for token in self._mention_tokens())

    async def _handle_event(self, uid: str, event: live_signals.LiveEvent) -> None:
        kind = event.kind
        if kind == live_signals.EVT_COMMENT:
            self._danmaku.setdefault(uid, deque(maxlen=self._window_size())).append({
                "ts": time.time(), "uid": event.user_id, "name": event.user_name,
                "text": event.text[:200],
            })
            if self._is_mention(event.text):
                await self._dispatch_danmaku(uid, event, is_to_me=True, trigger=True)
            elif self._channel.config.live_record_chatter:
                await self._dispatch_danmaku(uid, event, is_to_me=False, trigger=False)
            return
        if kind in (live_signals.EVT_LIKE, live_signals.EVT_ENTER):
            return  # 计数已在连接层落，仅作背景噪音
        if kind == live_signals.EVT_DISPLAY:
            return  # 计数由连接层 stats 持有，上下文直接读
        if kind == live_signals.EVT_GIFT:
            name = await self._resolve_gift_name(uid, event.extra.get("gift_id", ""))
            count = event.extra.get("count", 1)
            combo = event.extra.get("combo", 1)
            desc = f"送出 {name}×{count}" + (f"（{combo} 连击）" if combo > 1 else "")
            self._gift_feed.setdefault(uid, deque(maxlen=10)).append(f"{event.user_name} {desc}")
            await self._dispatch_special(uid, event, desc, trigger=bool(self._channel.config.live_gift_trigger_mind))
            return
        if kind == live_signals.EVT_BANANA:
            count = event.extra.get("count", 1)
            desc = f"投了 {count} 根香蕉"
            self._gift_feed.setdefault(uid, deque(maxlen=10)).append(f"{event.user_name} {desc}")
            await self._dispatch_special(uid, event, desc, trigger=bool(self._channel.config.live_gift_trigger_mind))
            return
        if kind == live_signals.EVT_FOLLOW:
            await self._dispatch_special(uid, event, "关注了主播", trigger=False)
            return
        if kind == live_signals.EVT_JOIN_CLUB:
            await self._dispatch_special(uid, event, "加入了粉丝团", trigger=bool(self._channel.config.live_gift_trigger_mind))
            return
        if kind == live_signals.EVT_KICKED:
            await self._dispatch_system(uid, f"连接被服务端踢出：{event.text}", trigger=True)
            return
        if kind == live_signals.EVT_VIOLATION:
            await self._dispatch_system(uid, f"直播间违规警告：{event.text}", trigger=True)
            return
        if kind == live_signals.EVT_STATUS_CHANGED:
            change = event.extra.get("change", "unknown")
            await self._dispatch_system(uid, f"直播状态变更：{change}", trigger=False)
            return

    async def _handle_state(self, uid: str, state: RoomState, detail: str) -> None:
        """连接状态变更 → 系统消息进房间会话（供历史审计与 AI 感知）。"""
        if state == RoomState.CONNECTED:
            message = f"直播连接已建立：{detail}"
        elif state == RoomState.RECONNECTING:
            message = f"直播连接断开，正在重连（{detail}）"
        elif state == RoomState.CLOSED:
            message = f"直播间当前不可观看：{detail}"
        else:
            return
        self._state_events.append(f"{int(time.time())} live:{uid} {message}")
        await self._dispatch_system(uid, message, trigger=False)

    # ------------------------------------------------------------------
    # 派发与快照
    # ------------------------------------------------------------------

    async def _dispatch_danmaku(self, uid: str, event: live_signals.LiveEvent,
                                *, is_to_me: bool, trigger: bool) -> None:
        from agent.channel.schemas import AdapterChannel, AdapterMessage, AdapterUser, ChannelType

        message = AdapterMessage(
            sender=AdapterUser(platform="acfun", user_id=event.user_id or "unknown",
                               user_name=event.user_name or "unknown"),
            channel=AdapterChannel(channel_id=f"live:{uid}", channel_type=ChannelType.GROUP,
                                   channel_name="直播间"),
            content=event.text,
            is_to_me=is_to_me,
            trigger_mind=trigger,
        )
        await self._channel.on_message(message)

    async def _dispatch_special(self, uid: str, event: live_signals.LiveEvent,
                                desc: str, *, trigger: bool) -> None:
        from agent.channel.schemas import AdapterChannel, AdapterMessage, AdapterUser, ChannelType

        message = AdapterMessage(
            sender=AdapterUser(platform="acfun", user_id=event.user_id or "unknown",
                               user_name=event.user_name or "unknown"),
            channel=AdapterChannel(channel_id=f"live:{uid}", channel_type=ChannelType.GROUP,
                                   channel_name="直播间"),
            content=f"[直播间事件] {event.user_name} {desc}",
            is_to_me=False,
            trigger_mind=trigger,
        )
        await self._channel.on_message(message)

    async def _dispatch_system(self, uid: str, text: str, *, trigger: bool) -> None:
        from agent.channel.schemas import AdapterChannel, AdapterMessage, AdapterUser, ChannelType

        message = AdapterMessage(
            sender=AdapterUser(platform="acfun", user_id="acfun_live", user_name="AcFun 直播"),
            channel=AdapterChannel(channel_id=f"live:{uid}", channel_type=ChannelType.GROUP,
                                   channel_name="直播间"),
            content=f"[系统] {text}",
            is_to_me=False,
            trigger_mind=trigger,
        )
        await self._channel.on_message(message)

    async def _resolve_gift_name(self, uid: str, gift_id: str) -> str:
        """礼物 ID → 名称（HTTP 惰性解析，按房间缓存）。"""
        names = self._gift_names.setdefault(uid, {})
        if gift_id in names:
            return names[gift_id]
        try:
            gifts = await self._channel.client.run(self._channel.client.live_gift_list, uid)
            names.update({gid: str(g.get("name") or gid) for gid, g in (gifts or {}).items()})
        except Exception as exc:
            log(f"AcFun直播: 礼物列表解析失败 live:{uid}: {exc}", "DEBUG", tag="通道")
        return names.get(gift_id, f"礼物#{gift_id}")

    # ------------------------------------------------------------------
    # 快照（上下文 provider / 状态 / 诊断工具共用，纯内存读）
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """当前直播会话总览。"""
        rooms: List[Dict[str, Any]] = []
        now = time.time()
        for uid, conn in self._rooms.items():
            stats = conn.stats
            rooms.append({
                "uid": uid,
                "state": conn.state.value,
                "detail": conn.state_detail,
                "title": self._room_title(uid, conn),
                "user_name": getattr(conn._params, "user_name", ""),  # noqa: SLF001
                "uptime": (now - stats.connected_since) if stats.connected_since else 0,
                "watching": stats.watching,
                "likes": stats.like_total,
                "banana": stats.banana_total,
                "danmaku_recent": self._recent_danmaku_count(uid),
                "recent_danmaku": [
                    {"uid": x["uid"], "name": x["name"], "text": x["text"]}
                    for x in self.recent_danmaku(uid, limit=8)
                ],
                "recent_gifts": self.recent_gifts(uid, limit=5),
                "stats": {
                    "danmaku": stats.danmaku, "likes": stats.likes, "enters": stats.enters,
                    "follows": stats.follows, "gifts": stats.gifts,
                    "unknown_signals": stats.unknown_signals, "reconnects": stats.reconnects,
                    "ticket_rotations": stats.ticket_rotations, "param_refreshes": stats.param_refreshes,
                    "last_error": stats.last_error,
                    "last_signal_age": (now - stats.last_signal_at) if stats.last_signal_at else None,
                },
            })
        return {
            "mode": self._mode,
            "watched": list(self._watched),
            "rooms": rooms,
            "state_events": list(self._state_events)[-5:],
        }

    def _room_title(self, uid: str, conn: LiveRoomConnection) -> str:
        params = getattr(conn, "_params", None)
        return getattr(params, "title", "") if params is not None else ""

    def _recent_danmaku_count(self, uid: str) -> int:
        buffer = self._danmaku.get(uid)
        if not buffer:
            return 0
        deadline = time.time() - _CHATTER_WINDOW_SECONDS
        return sum(1 for item in buffer if item["ts"] >= deadline)

    def recent_danmaku(self, uid: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """最近弹幕（上下文渲染用，可带时间窗过滤）。"""
        buffer = self._danmaku.get(uid)
        if not buffer:
            return []
        deadline = time.time() - _CHATTER_WINDOW_SECONDS
        items = [x for x in buffer if x["ts"] >= deadline]
        if limit is not None:
            items = items[-limit:]
        return items

    def recent_gifts(self, uid: str, limit: int = 5) -> List[str]:
        feed = self._gift_feed.get(uid)
        if not feed:
            return []
        return list(feed)[-limit:]
