"""FunASR 流式会话背压治理测试：partial 至多一个在途、COMMIT 即时。

回归自：滚动窗 partial 内联 await 慢转写时帧队列无界积压，COMMIT 排在
队尾导致说完半天不定稿（部分转写 1.4s+/拍、单拍最坏挂到 HTTP 超时）。
"""

from __future__ import annotations

import asyncio

import pytest

from entities.audiosync.funasr_stream import _FunAsrStreamSession

RATE = 16000
FRAME = b"\x01\x02" * int(RATE * 0.06)  # 60ms 帧


class _SlowTranscribe:
    """可控慢转写：partial 挂起直到放行；定稿即时。"""

    def __init__(self) -> None:
        self.partial_calls = 0
        self.final_calls = 0
        self.release = asyncio.Event()

    async def __call__(self, pcm: bytes, sample_rate: int, *, partial: bool):
        if partial:
            self.partial_calls += 1
            await self.release.wait()
            return f"部分{self.partial_calls}"
        self.final_calls += 1
        return "定稿文本", [{"start_ms": 0, "end_ms": 1000, "text": "定稿文本"}]


class TestPartialBackpressure:
    async def test_accept_pcm_never_blocks_on_slow_partial(self) -> None:
        """慢转写期间 accept_pcm 立即返回（不在消费任务里内联等 HTTP）。"""
        tc = _SlowTranscribe()
        session = _FunAsrStreamSession(RATE, tc)
        # 第一拍点火 partial（挂起）；后续拍在途跳过
        for _ in range(30):  # ~1.8s 语音 → 3 个节拍点
            await asyncio.wait_for(session.accept_pcm(FRAME, RATE), timeout=0.2)
            await asyncio.sleep(0.07)
        assert tc.partial_calls == 1  # 在途未归，节拍全部跳过

    async def test_partial_event_drained_by_later_frames(self) -> None:
        """在途 partial 完成后经后续 accept_pcm 收转（队列模型）。"""
        tc = _SlowTranscribe()
        session = _FunAsrStreamSession(RATE, tc)
        await session.accept_pcm(FRAME, RATE)
        await asyncio.sleep(0)
        assert tc.partial_calls == 1
        tc.release.set()
        await asyncio.sleep(0.05)  # 让在途 partial 完成并入队
        # 下一帧收转到上一拍的结果
        events = await session.accept_pcm(FRAME, RATE)
        assert [e.kind for e in events] == ["partial"]
        assert events[0].text == "部分1"

    async def test_commit_immediate_and_cancels_inflight(self) -> None:
        """COMMIT 不等在途 partial：取消它并即时整段定稿。"""
        tc = _SlowTranscribe()
        session = _FunAsrStreamSession(RATE, tc)
        await session.accept_pcm(FRAME, RATE)
        await asyncio.sleep(0)
        assert tc.partial_calls == 1
        events = await asyncio.wait_for(session.commit(), timeout=1.0)
        assert tc.final_calls == 1
        assert [e.kind for e in events] == ["final"]
        assert events[0].text == "定稿文本"
        assert events[0].segments[0]["text"] == "定稿文本"

    async def test_partial_failure_skips_silently(self) -> None:
        """partial 失败静默（展示辅助不阻断语音流），节拍继续。"""

        async def _boom(pcm: bytes, sample_rate: int, *, partial: bool):
            if partial:
                raise RuntimeError("backend down")
            return "定稿", []

        session = _FunAsrStreamSession(RATE, _boom)
        await session.accept_pcm(FRAME, RATE)
        await asyncio.sleep(0.05)  # 让在途 partial 跑完（失败）
        events = await session.accept_pcm(FRAME, RATE)
        assert events == []
        # 失败不阻断定稿
        final = await session.commit()
        assert final[0].text == "定稿"


@pytest.fixture(autouse=True)
def _fast_beat(monkeypatch: pytest.MonkeyPatch):
    """节拍窗口收到最小（测试节奏可控，不等真实 600ms）。"""
    from core.config import ConfigManager

    ConfigManager.set("audiosync_stream_step_ms", 60)
    ConfigManager.set("audiosync_stream_window_ms", 4000)
    yield
    ConfigManager.set("audiosync_stream_step_ms", 600)
