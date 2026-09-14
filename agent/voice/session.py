"""语音会话管理器：租约、缓冲、能量法端点检测、WAV 成段交付。

配置项（core.config 热读取，配置中心自动可见）：
- voice_silence_ms：静音判段阈值（默认 800ms 连续静音收束成段）
- voice_min_utterance_ms：有效语音段最短时长（过短视为误触发丢弃）
- voice_max_utterance_s：单段最长时长（超时强制切段，防无限缓冲）
- voice_vad_floor_min：端点检测噪声地板下限（RMS，自适应地板的保守底）
"""

from __future__ import annotations

import asyncio
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from core.audio_frames import AudioFrame, EnergyVad
from core.config import register_configs_safe
from core.latebind import LateBinding, WireError
from core.log import log

_LOG_TAG = "语音"

register_configs_safe({
    "voice": {
        "voice_silence_ms": {
            "description": "语音端点检测：连续静音多少毫秒后收束成段",
            "default": 800, "unit": "ms", "min": 200, "max": 5000,
        },
        "voice_min_utterance_ms": {
            "description": "有效语音段最短时长（低于视为误触发丢弃）",
            "default": 300, "unit": "ms", "min": 0, "max": 5000,
        },
        "voice_max_utterance_s": {
            "description": "单段语音最长时长（超时强制切段）",
            "default": 30, "unit": "s", "min": 5, "max": 300,
        },
        "voice_vad_floor_min": {
            "description": "端点检测噪声地板下限（RMS；地板按近 5s 滑窗自适应，此为其保守下限）",
            "default": 100, "min": 20, "max": 2000, "advanced": True,
        },
    },
})


class VoiceLeaseBusy(RuntimeError):
    """同一 owner 已有进行中的语音会话（租约冲突，显式拒绝而非顶号）。"""


@dataclass(slots=True)
class VoiceDelivery:
    """语音段的投递上下文（开启会话时绑定，成段后随 utterance 走）。"""

    user_id: str = "web_user"
    user_name: str = "用户"
    session_id: str = ""
    """多会话 chat_id（写入 Everything.session_id 参与 scope 隔离）。"""
    adapter_key: str = "webui"
    """落点频道（决定 entity_scope 与回复路由）。"""


@dataclass(slots=True)
class VoiceUtterance:
    """一段收束完成的语音（sink 的交付单元）。"""

    owner: str
    """会话归属（开启方标识，如 WS 连接 identity）。"""
    file_path: str
    """WAV 落盘路径（workspace/uploads/voice/）。"""
    sample_rate: int
    duration_ms: float
    started_at: float
    delivery: VoiceDelivery = field(default_factory=VoiceDelivery)
    transcript: str = ""
    """转写文本（Realtime/ASR 管线接入后填充；接口铺垫阶段恒为空）。"""


# sink 端口：成段语音的统一交付出口（跨层桥成因；由组合根
# agent/runtime/wiring.py 施绑 deliver_utterance，经 AgentApp 统一入口
# 转 Everything 进入消息管线；未施绑时成段仅广播事件）
voice_sink_port: LateBinding["VoiceSink"] = LateBinding("voice_utterance_sink")

VoiceSink = Callable[[VoiceUtterance], Awaitable[None]]


@dataclass
class _Session:
    owner: str
    connection_id: str
    sample_rate: int
    delivery: VoiceDelivery = field(default_factory=VoiceDelivery)
    started_at: float = field(default_factory=time.time)
    buffer: bytearray = field(default_factory=bytearray)
    vad: Optional[EnergyVad] = None
    duration_ms: float = 0.0
    speech_ms: float = 0.0
    silence_ms: float = 0.0
    has_speech: bool = False
    watchdog: Optional[asyncio.TimerHandle] = None


class VoiceSessionManager:
    """语音会话管理器（进程内单例，经 get_voice_manager 获取）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}

    @staticmethod
    def _cfg() -> tuple[float, float, float]:
        from core.config import get_config_float, get_config_int
        return (
            get_config_int("voice_silence_ms", 800),
            get_config_int("voice_min_utterance_ms", 300),
            get_config_float("voice_max_utterance_s", 30.0),
        )

    def start_session(
        self,
        owner: str,
        connection_id: str,
        sample_rate: int,
        delivery: Optional[VoiceDelivery] = None,
    ) -> None:
        """开启语音会话（MicLease 式租约：同 owner 已有会话则显式拒绝）。

        delivery 绑定本会话产出语音段的投递上下文（用户/会话/落点频道），
        成段后随 utterance 交给 sink——session 层不关心消息如何投递。
        """
        existing = self._sessions.get(owner)
        if existing is not None:
            raise VoiceLeaseBusy(
                f"已有进行中的语音会话（connection={existing.connection_id}）"
            )
        from core.config import get_config_float
        self._sessions[owner] = _Session(
            owner=owner, connection_id=connection_id, sample_rate=sample_rate,
            delivery=delivery or VoiceDelivery(),
            vad=EnergyVad(floor_min=get_config_float("voice_vad_floor_min", 100.0)),
        )
        log(f"语音会话开始: owner={owner} rate={sample_rate}", "DEBUG", tag=_LOG_TAG)

    def owns(self, owner: str, connection_id: str) -> bool:
        """连接是否持有该 owner 的语音租约（帧鉴权用）。"""
        session = self._sessions.get(owner)
        return session is not None and session.connection_id == connection_id

    async def accept_frame(self, owner: str, connection_id: str, frame: AudioFrame) -> None:
        """接收一条音频帧：缓冲 + 端点检测，到段即 finalize。"""
        session = self._sessions.get(owner)
        if session is None or session.connection_id != connection_id:
            return  # 无租约的帧静默丢弃（客户端协议错误由控制面拒绝，不逐帧报错）
        silence_ms, _min_ms, max_s = self._cfg()

        session.buffer.extend(frame.pcm)
        session.duration_ms += frame.duration_ms
        if session.vad is not None and session.vad.is_speech(frame.pcm):
            session.silence_ms = 0.0
            session.has_speech = True
            session.speech_ms += frame.duration_ms
        else:
            session.silence_ms += frame.duration_ms

        if session.duration_ms >= max_s * 1000:
            await self._finalize(session, reason="时长上限")
            return
        if session.has_speech and session.silence_ms >= silence_ms:
            await self._finalize(session, reason="静音收束")
            return
        self._arm_watchdog(session, silence_ms)

    async def end_session(self, owner: str, connection_id: str) -> None:
        """结束语音会话：未收束的缓冲立即成段交付。"""
        session = self._sessions.get(owner)
        if session is None or session.connection_id != connection_id:
            return
        await self._finalize(session, reason="会话结束")

    async def drop_connection(self, connection_id: str) -> None:
        """连接断开清理：该连接持有的全部会话成段收尾（不丢已录语音）。"""
        for _owner, session in list(self._sessions.items()):
            if session.connection_id == connection_id:
                await self._finalize(session, reason="连接断开")

    # ------------------------------------------------------------------
    # 内部：看门狗与成段
    # ------------------------------------------------------------------

    def _arm_watchdog(self, session: _Session, silence_ms: float) -> None:
        """（重）设看门狗：最后一帧之后无新帧也按静音阈值+一帧宽限 finalize。"""
        if session.watchdog is not None:
            session.watchdog.cancel()
        loop = asyncio.get_running_loop()
        session.watchdog = loop.call_later(
            (silence_ms + 200) / 1000,
            lambda s=session: asyncio.ensure_future(self._watchdog_fire(s)),
        )

    async def _watchdog_fire(self, session: _Session) -> None:
        if self._sessions.get(session.owner) is not session:
            return
        # 无条件收束：纯静音/停滞会话由 _finalize 按阈值丢弃并释放租约
        # （否则卡死的无声会话会一直占用租约，后续语音会话全被拒绝）
        await self._finalize(session, reason="静音看门狗")

    async def _finalize(self, session: _Session, *, reason: str) -> None:
        """成段交付：校验最小时长 → 写 WAV → 交 sink；会话从注册表摘除。"""
        if self._sessions.pop(session.owner, None) is None:
            return
        if session.watchdog is not None:
            session.watchdog.cancel()
        _silence_ms, min_ms, _max_s = self._cfg()
        if not session.has_speech or session.speech_ms < min_ms:
            log(
                f"语音段丢弃（{reason}，有声 {session.speech_ms:.0f}ms 低于阈值）: "
                f"owner={session.owner}",
                "DEBUG", tag=_LOG_TAG,
            )
            return
        try:
            file_path = await asyncio.to_thread(self._write_wav, session)
        except Exception as exc:
            log(f"语音段写盘失败: {exc}", "WARNING", tag=_LOG_TAG)
            return
        log(
            f"语音段成段（{reason}）: {session.duration_ms:.0f}ms → {file_path}",
            "DEBUG", tag=_LOG_TAG,
        )
        utterance = VoiceUtterance(
            owner=session.owner,
            file_path=file_path,
            sample_rate=session.sample_rate,
            duration_ms=session.duration_ms,
            started_at=session.started_at,
            delivery=session.delivery,
        )
        await self._deliver(utterance)

    @staticmethod
    def _write_wav(session: _Session) -> str:
        from core.path import ConfigPaths
        out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / "voice"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.wav"
        dest = out_dir / name
        with wave.open(str(dest), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(session.sample_rate)
            wf.writeframes(bytes(session.buffer))
        return str(dest)

    async def _deliver(self, utterance: VoiceUtterance) -> None:
        """交付给 sink 端口（并广播事件供钩子面/未来 Realtime 管线消费；fail-open）。"""
        from core.event_bus import EVENT_VOICE_UTTERANCE, event_bus
        await event_bus.emit(EVENT_VOICE_UTTERANCE, {
            "owner": utterance.owner,
            "file_path": utterance.file_path,
            "duration_ms": utterance.duration_ms,
            "sample_rate": utterance.sample_rate,
        })
        try:
            sink = voice_sink_port.get()
        except WireError:
            log("语音 sink 端口未施绑，语音段未接入消息管线", "DEBUG", tag=_LOG_TAG)
            return
        try:
            await sink(utterance)
        except Exception as exc:
            log(f"语音段交付失败: {exc}", "WARNING", tag=_LOG_TAG)

    def reset(self) -> None:
        """清空全部会话（测试用）。"""
        for session in self._sessions.values():
            if session.watchdog is not None:
                session.watchdog.cancel()
        self._sessions.clear()


_manager: Optional[VoiceSessionManager] = None


def get_voice_manager() -> VoiceSessionManager:
    """进程内单例。"""
    global _manager
    if _manager is None:
        _manager = VoiceSessionManager()
    return _manager
