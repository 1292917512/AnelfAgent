"""播报车道 — 会话内全部 TTS 播报的串行化、优先级与归因仲裁。

三个不变量（实时响应仲裁的通用内核，落地到级联管线）：

1. **单工车道**：同一时刻至多一个"生产中"的播报单元向播放队列写帧。
   多来源（语音轮回复流 / send_message 自动路由的主动播报）并发到达
   时由车道串行化——绝不允许两个生产任务交错写队列（音频混杂）。
2. **优先级与抢占**：用户轮回复（PRIORITY_REPLY）优先于主动播报
   （PRIORITY_SPEAK）。回复就绪时抢占在播的主动消息——取消其生产，
   已入队音频照常排空（听感是"说完这句就回应你"）；主动消息绝不
   抢占回复流，也不互相抢占，排在车道里等当前单元收尾后依序播出。
3. **归因与有界结算**：每个播报单元持单调 uid；写入侧逐块自检 uid
   仍是活动单元。被取代/打断的任务不再写帧、不再迁移会话状态、不
   重复 audio_done——收尾权只属于车道当前活动单元。

车道由生产任务驱动推进（无独立 worker 协程）：活动单元的 settled()
在其 finally 里被调用，触发下一单元启动。打断（barge-in）/会话关闭
走 reset()：活动单元标记取代并取消，队列清空。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from core.log import log

_LOG_TAG = "实时语音"

PRIORITY_REPLY = 0
"""语音轮回复流（用户发起）——最高播报优先级。"""

PRIORITY_SPEAK = 10
"""主动播报（send_message 自动路由/提醒）——可被回复抢占，彼此按序。"""

StarterFn = Callable[["Utterance"], "asyncio.Task[None]"]
"""生产任务工厂：车道启动单元时调用（排队单元在轮到之前不建任务）。"""


@dataclass
class Utterance:
    """一个播报单元（一段完整的"要说的话"的生产与收尾权）。"""

    uid: int
    turn_id: int
    priority: int
    source: str
    """reply（回复流）/ speak（主动播报）——诊断与状态展示用。"""
    task: Optional["asyncio.Task[None]"] = None
    superseded: bool = False
    """被抢占/打断/会话关闭取代：不再写帧、不再收尾。"""
    spoken: bool = False
    """自然播完（收束帧已入队）——voice_spoken 语义的唯一判据。"""
    on_spoken: Optional[Callable[["Utterance"], Awaitable[None]]] = None
    """自然播完回调（语音形态标记等）；取消/失败不触发。"""
    produced: int = 0
    """已产出块数（零产出 = 合成失败，收尾策略不同）。"""


@dataclass
class SpeakLane:
    """单会话播报车道（会话独享；事件循环内无锁——同会话事件天然串行）。"""

    _seq: int = 0
    _queue: List[Utterance] = field(default_factory=list)
    _active: Optional[Utterance] = None
    _starters: Dict[int, StarterFn] = field(default_factory=dict)
    """排队单元的启动器挂账（uid → starter）：轮到时取用后即销。"""
    _live: set = field(default_factory=set)
    """在产任务集合（强引用防 GC + 关停时有界结算的对象）。"""

    @property
    def active(self) -> Optional[Utterance]:
        return self._active

    def is_active(self, u: Utterance) -> bool:
        """写入侧逐块自检：u 是否仍是车道活动单元（未被取代）。"""
        return u is self._active and not u.superseded

    def submit(
        self,
        *,
        turn_id: int,
        priority: int,
        source: str,
        starter: StarterFn,
        on_spoken: Optional[Callable[[Utterance], Awaitable[None]]] = None,
    ) -> Utterance:
        """登记播报单元并按车道策略启动或排队。

        回复（更高优先级）抢占在播的低优先级单元；其余情形排队等待
        当前单元自然收尾。车道空闲时立即启动（starter 就地建任务）。
        """
        self._seq += 1
        u = Utterance(
            uid=self._seq, turn_id=turn_id, priority=priority,
            source=source, on_spoken=on_spoken,
        )
        active = self._active
        if active is not None and self._is_running(active):
            if priority < active.priority:
                self._supersede(active, reason=f"被 {source}#{u.uid} 抢占")
        self._starters[u.uid] = starter
        self._queue.append(u)
        self._advance()
        return u

    def settled(self, u: Utterance) -> None:
        """活动单元生产结束（其 finally 调用，覆盖完成/取消/异常全部出口）：
        让出车道并推进启动下一个排队单元。非活动单元调用为 no-op。"""
        if self._active is not u:
            return
        self._active = None
        self._advance()

    def finish(self, u: Utterance, *, push_final: Callable[[Utterance], None]) -> bool:
        """活动单元的自然收尾：入队收束帧（audio_done）并触发 spoken 回调。

        push_final 由调用方注入（携带播放队列与 turn 语义）；非活动单元
        （已被取代）返回 False 不收尾——收尾权独占是"不重复 audio_done"
        的根基。spoken 回调只触发一次。
        """
        if not self.is_active(u) or u.spoken:
            return False
        u.spoken = True
        push_final(u)
        if u.on_spoken is not None:
            callback = u.on_spoken

            async def _notify() -> None:
                try:
                    await callback(u)
                except Exception as exc:
                    log(f"播报完成回调失败 #{u.uid}: {exc}", "DEBUG", tag=_LOG_TAG)

            asyncio.get_running_loop().create_task(_notify())
        return True

    def reset(self) -> None:
        """打断/会话关闭：活动单元取代并取消，队列整体作废。"""
        active = self._active
        if active is not None:
            self._supersede(active, reason="车道重置（打断/关闭）")
        for u in self._queue:
            u.superseded = True
        self._queue.clear()
        self._starters.clear()
        self._active = None
        for task in list(self._live):
            if not task.done():
                task.cancel()

    async def settle(self, timeout: float = 2.0) -> None:
        """重置车道并有界等待全部在产任务退出（会话关闭的收尾保障）。"""
        self.reset()
        if self._live:
            await asyncio.wait(set(self._live), timeout=timeout)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _is_running(u: Utterance) -> bool:
        return not u.superseded and u.task is not None and not u.task.done()

    def _advance(self) -> None:
        """推进车道：无活动单元时按 (priority, uid) 取下一个排队单元启动。"""
        active = self._active
        if active is not None and self._is_running(active):
            return
        self._active = None
        if not self._queue:
            return
        self._queue.sort(key=lambda u: (u.priority, u.uid))
        nxt = self._queue.pop(0)
        starter = self._starters.pop(nxt.uid, None)
        if starter is None:
            # 防御：挂账丢失（reset 竞态等）——作废该单元继续推进，车道不卡死
            log(f"播报单元 #{nxt.uid}({nxt.source}) 缺启动器，作废跳过", "WARNING", tag=_LOG_TAG)
            nxt.superseded = True
            self._advance()
            return
        self._active = nxt
        nxt.task = starter(nxt)
        self._live.add(nxt.task)
        nxt.task.add_done_callback(self._live.discard)

    def _supersede(self, u: Utterance, *, reason: str) -> None:
        """取代一个单元：标记 + 取消生产任务（结算由任务 finally 走 settled）。"""
        u.superseded = True
        if u.task is not None and not u.task.done():
            u.task.cancel()
        log(f"播报单元 #{u.uid}({u.source}) {reason}", "DEBUG", tag=_LOG_TAG)
