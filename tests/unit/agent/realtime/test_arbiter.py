"""播报车道（SpeakLane）单元测试：串行化 / 优先级抢占 / 归因收尾。"""

from __future__ import annotations

import asyncio

from agent.realtime.arbiter import (
    PRIORITY_REPLY,
    PRIORITY_SPEAK,
    SpeakLane,
    Utterance,
)


def _run_lane(lane: SpeakLane, log: list, specs: list, gate: asyncio.Event | None = None):
    """按 specs [(priority, source)] 依次 submit；生产循环每块自检活动身份。

    uid 为 1 的单元在第二块后经 gate 挂起（模拟合成耗时/抢占窗口）。
    """
    def starter(u: Utterance) -> asyncio.Task:
        async def _produce() -> None:
            try:
                for i in range(3):
                    if gate is not None and u.uid == 1 and i == 1:
                        await gate.wait()
                    if not lane.is_active(u):
                        log.append((u.uid, "aborted"))
                        return
                    log.append((u.uid, i))
                    await asyncio.sleep(0)
                log.append((u.uid, "end"))
            finally:
                lane.settled(u)
        return asyncio.create_task(_produce())

    for priority, source in specs:
        lane.submit(
            turn_id=1, priority=priority, source=source, starter=starter,
        )


class TestSerialization:
    async def test_speaks_queue_fully(self):
        """两个主动播报按序全播，绝不交错。"""
        lane = SpeakLane()
        log: list = []
        _run_lane(lane, log, [(PRIORITY_SPEAK, "speak"), (PRIORITY_SPEAK, "speak")])
        await asyncio.sleep(0.2)
        # 第一单元完整播完（1,2,3,end）后第二单元才开始
        assert log[:5] == [(1, 0), (1, 1), (1, 2), (1, "end"), (2, 0)]

    async def test_reply_preempts_active_speak(self):
        """回复抢占在播主动消息：旧单元立即停写，新单元接管。"""
        gate = asyncio.Event()
        lane = SpeakLane()
        log: list = []

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    for i in range(5):
                        if u.uid == 1 and i == 2:
                            await gate.wait()  # 主动消息播到一半挂起
                        if not lane.is_active(u):
                            log.append((u.uid, "aborted"))
                            return
                        log.append((u.uid, i))
                        await asyncio.sleep(0)
                    log.append((u.uid, "end"))
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        await asyncio.sleep(0.05)
        lane.submit(turn_id=1, priority=PRIORITY_REPLY, source="reply", starter=starter)
        gate.set()
        await asyncio.sleep(0.1)
        # 新单元从 0 开始完整播出并收尾
        assert log[-1] == (2, "end")
        # 被抢占的旧单元在取消点（gate 挂起处）彻底停止：其后没有任何 uid=1 的写帧
        first_of_new = log.index((2, 0))
        assert all(e[0] != 1 for e in log[first_of_new:])

    async def test_speak_never_preempts_reply(self):
        """主动消息不抢占回复：排队等回复自然收尾。"""
        gate = asyncio.Event()
        lane = SpeakLane()
        log: list = []

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    for i in range(3):
                        if u.uid == 1 and i == 1:
                            await gate.wait()
                        log.append((u.uid, i))
                        await asyncio.sleep(0)
                    log.append((u.uid, "end"))
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(turn_id=1, priority=PRIORITY_REPLY, source="reply", starter=starter)
        await asyncio.sleep(0.05)
        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        await asyncio.sleep(0.05)
        assert log == [(1, 0)]  # 回复挂着（gate），排队的 speak 不动
        gate.set()
        await asyncio.sleep(0.1)
        assert log == [(1, 0), (1, 1), (1, 2), (1, "end"), (2, 0), (2, 1), (2, 2), (2, "end")]


class TestFinishExclusivity:
    async def test_finish_only_active(self):
        """收尾权独占：被取代单元 finish 返回 False（不重复 audio_done）。"""
        lane = SpeakLane()
        finals: list = []
        gate = asyncio.Event()

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    if u.uid == 1:
                        await gate.wait()
                    if not lane.is_active(u):
                        return
                    lane.finish(u, push_final=lambda x: finals.append(x.uid))
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        await asyncio.sleep(0.02)
        # 单元 1 挂起时回复抢占 → 单元 1 恢复后 finish 应被拒
        lane.submit(turn_id=1, priority=PRIORITY_REPLY, source="reply", starter=starter)
        gate.set()
        await asyncio.sleep(0.05)
        assert finals == [2]  # 只有活动单元 2 的收尾生效

    async def test_spoken_callback_once(self):
        """spoken 回调只触发一次且仅在自然收尾时。"""
        lane = SpeakLane()
        fired: list = []

        async def _on_spoken(u: Utterance) -> None:
            fired.append(u.uid)

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    if lane.is_active(u):
                        lane.finish(u, push_final=lambda x: None)
                        lane.finish(u, push_final=lambda x: None)  # 二次收尾 no-op
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(
            turn_id=1, priority=PRIORITY_SPEAK, source="speak",
            starter=starter, on_spoken=_on_spoken,
        )
        await asyncio.sleep(0.05)
        assert fired == [1]


class TestReset:
    async def test_reset_cancels_active_and_queue(self):
        lane = SpeakLane()
        gate = asyncio.Event()
        log: list = []

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    if u.uid == 1:
                        await gate.wait()
                    log.append((u.uid, "ran"))
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        await asyncio.sleep(0.02)
        lane.reset()
        gate.set()
        await asyncio.sleep(0.05)
        assert log == []  # 活动单元被取消，排队单元作废
        assert lane.active is None

    async def test_settle_bounded(self):
        """settle 有界等待在产任务退出（卡死任务不拖垮关闭）。"""
        lane = SpeakLane()

        def starter(u: Utterance) -> asyncio.Task:
            async def _produce() -> None:
                try:
                    await asyncio.sleep(30)  # 模拟卡死（不吃取消？会吃：cancel 生效）
                finally:
                    lane.settled(u)
            return asyncio.create_task(_produce())

        lane.submit(turn_id=1, priority=PRIORITY_SPEAK, source="speak", starter=starter)
        await asyncio.sleep(0.02)
        await asyncio.wait_for(lane.settle(timeout=0.5), timeout=2.0)  # 取消生效，很快返回
        assert not lane._live
