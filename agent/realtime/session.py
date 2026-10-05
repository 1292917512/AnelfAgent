"""实时语音会话 — 状态机、轮次令牌、打断与播放写任务。

一个 RealtimeSession 对应一条全双工语音对话通道（一个 WS 连接一个）：
- LISTENING：接收麦克风帧 → 端点检测 → 流式 ASR（partial 展示）；
- THINKING：语音收束定稿 → 用户消息经统一入口进思维（等回复）；
- SPEAKING：回复增量文本 → TTS 管线 → 播放队列 → 下行音频帧。

轮次令牌（turn_id）：每轮用户语音 +1，贯穿 ASR 定稿 → 思维 → TTS →
播放帧——打断（barge-in）即 turn_id+1 并清空上一轮的合成/播放链路，
下行端凭 turn_id 丢弃过期音频（听感"立刻闭嘴"）。

打断语义：SPEAKING/THINKING 中检测到新的语音起始且 realtime_barge_in
开启 → 取消 TTS 任务、清空播放队列、发 interrupted 收束帧、回到
LISTENING 收听新一段；思维的文本回复不打断（继续流入聊天记录）。
"""

from __future__ import annotations

import asyncio
import enum
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Set, Tuple

from agent.realtime.arbiter import SpeakLane
from agent.realtime.playback import PcmResampler, PlaybackQueue
from agent.voice.preprocess import PcmPreprocessor, create_preprocessor
from agent.voice.session import VoiceDelivery
from agent.voice.turn_detection import TurnDetector, create_turn_detector
from core.log import log

_LOG_TAG = "实时语音"

SendAudio = Callable[[bytes, int], Awaitable[None]]
"""下行音频帧（pcm, sample_rate）——连接层负责编码为二进制帧。"""
SendEvent = Callable[[str, Dict[str, Any]], Awaitable[None]]
"""下行 JSON 事件（rt_state/rt_partial/rt_final/audio_done 等）。"""


@dataclass
class RealtimeSink:
    """会话的下行出口（由 WS 连接层注册；断连时随会话一并摘除）。"""

    send_audio: SendAudio
    send_event: SendEvent


class SessionState(enum.Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class CallMode(str, enum.Enum):
    """通话模式：轮次边界的裁决者（管线其余环节两模式共享）。"""

    FREE = "free"
    """自由通话：端点检测裁决轮次边界（常开麦全双工）。"""
    PTT = "ptt"
    """点按通话：按键信号裁决轮次边界（按下收音、松开收束，端点检测旁路）。"""


@dataclass
class RealtimeSession:
    """一条全双工语音会话（引擎每 owner 持有一个）。"""

    owner: str
    delivery: VoiceDelivery
    sample_rate: int
    sink: RealtimeSink
    state: SessionState = SessionState.LISTENING
    turn_id: int = 0
    detector: TurnDetector = field(init=False)
    """端点检测器（auto 梯队：语义端点 → VAD → 能量法）。"""
    preprocessor: PcmPreprocessor = field(init=False)
    """输入预处理链（降噪 → 增益 → 限幅；全关直通）。"""
    playback: PlaybackQueue = field(default_factory=PlaybackQueue)
    asr_session: Any = None
    """流式 ASR 会话（引擎在首个语音帧开启；无流式提供者时退化为整段缓冲）。"""
    asr_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    """ASR 喂帧队列——帧泵只入队零阻塞，网络转写在消费任务中执行
    （滚动窗转写单帧 1.4s > 60ms 帧间隔，泵上直接 await 会永久积压）。"""
    asr_consumer: Optional[asyncio.Task] = None
    """当前代 ASR 消费任务（诊断/展示用；生命周期管理走 asr_consumers）。"""
    asr_consumers: "Set[asyncio.Task]" = field(default_factory=set)
    """全部在产 ASR 消费任务（随代际轮换：旧任务处理完自己那代哨兵后
    自行退出；close 时统一取消，防无哨兵路径的任务泄漏）。"""
    pcm_buffer: bytearray = field(default_factory=bytearray)
    """整段 ASR 兜底缓冲 / 流式会话的原始音频留存。"""
    pre_roll: Deque[bytes] = field(default_factory=deque)
    """起始确认前的前滚帧（收听态的最近 ~300ms）：VAD onset 确认需要
    前导语音，确认前的帧不回补会吃掉首音节——确认入段时回补进
    ASR 与整段缓冲（pre-roll，对齐流式 VAD 通行实践）。"""
    _native_client: Any = None
    _native_resampler: Any = None
    _native_pump_task: Optional[asyncio.Task] = None
    barge_in_task: Optional[asyncio.Task] = None
    """打断确认任务（回声防护的观察窗；确认/取消/会话收尾时收束）。"""
    finalize_task: Optional[asyncio.Task] = None
    """语音收束处理任务（ASR 定稿/声纹/用户轮入库离线化，不阻塞麦克风帧流）。"""
    lane: SpeakLane = field(default_factory=SpeakLane)
    """播报车道：全部 TTS 播报的串行化与归因（生产任务由车道持有）。"""
    call_mode: CallMode = CallMode.FREE
    """通话模式（自由/点按；引擎在 start/set_call_mode 时改写）。"""
    ptt_active: bool = False
    """PTT 按键按下中（收音门控；仅 call_mode=ptt 时有意义）。"""
    tts_pipeline: Any = None
    """当前回复轮的 TTS 管线（引擎驱动；增量文本入口）。"""
    writer_task: Optional[asyncio.Task] = None
    last_say_error: str = ""
    """最近一次主动语音的失败原因（无失败为空；realtime_status 暴露给 AI 确认）。"""
    spoken_recent: Deque[Tuple[float, str]] = field(default_factory=deque)
    """近期已送 TTS 的文本（monotonic 时间戳, 归一化文本）——回声比对事实源。"""
    closed: bool = False

    def __post_init__(self) -> None:
        self.detector = create_turn_detector(self.sample_rate)
        self.preprocessor = create_preprocessor(self.sample_rate)

    # ------------------------------------------------------------------
    # 状态迁移（rt_state 事件随迁随发，前端状态条据此渲染）
    # ------------------------------------------------------------------

    async def set_state(self, state: SessionState) -> None:
        if self.state is state or self.closed:
            return
        self.state = state
        await self.sink.send_event("rt_state", {
            "state": state.value, "turn_id": self.turn_id,
        })

    def next_turn(self) -> int:
        self.turn_id += 1
        return self.turn_id

    # ------------------------------------------------------------------
    # 播放写任务：播放队列 → 重采样分帧 → 下行
    # ------------------------------------------------------------------

    def start_writer(self, playback_rate: int) -> None:
        self.writer_task = asyncio.create_task(
            self._writer_loop(playback_rate), name=f"rt.writer.{self.owner}")

    async def _writer_loop(self, playback_rate: int) -> None:
        resamplers: Dict[int, PcmResampler] = {}
        try:
            while not self.closed:
                frame = await self.playback.read()
                if frame is None:
                    # 打断哨兵：发 interrupted 收束帧（audio_done 帧对的打断形态）
                    await self.sink.send_event("audio_done", {
                        "turn_id": self.turn_id, "interrupted": True,
                    })
                    resamplers.clear()
                    continue
                if frame.final:
                    await self.sink.send_event("audio_done", {
                        "turn_id": frame.turn_id, "interrupted": False,
                    })
                    await self.set_state(SessionState.LISTENING)
                    resamplers.clear()
                    continue
                resampler = resamplers.get(frame.sample_rate)
                if resampler is None:
                    resampler = resamplers[frame.sample_rate] = PcmResampler(
                        frame.sample_rate, playback_rate)
                out = resampler.feed(frame.pcm)
                if out:
                    await self.sink.send_audio(out, playback_rate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"播放写任务异常: {exc}", "WARNING", tag=_LOG_TAG)
            try:
                await self.sink.send_event("rt_error", {
                    "level": "error", "message": "音频下行通道异常，请挂断后重新通话",
                })
            except Exception:
                pass

    async def interrupt(self) -> None:
        """barge-in：车道重置（取消一切播报生产与排队）、清空播放、回到收听。"""
        self.lane.reset()
        self.tts_pipeline = None
        self.playback.clear()
        self.detector.reset()
        self.preprocessor.reset()
        self.pre_roll.clear()
        await self.set_state(SessionState.LISTENING)

    async def close(self) -> None:
        """会话收尾：车道有界结算 + 取消全部任务（幂等）。"""
        self.closed = True
        await self.lane.settle(timeout=2.0)
        tasks = [t for t in (self.writer_task, self.barge_in_task, self.finalize_task,
                             *self.asr_consumers)
                 if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)
        if self.asr_session is not None:
            try:
                await self.asr_session.close()
            except Exception:
                pass
