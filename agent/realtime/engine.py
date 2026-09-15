"""实时语音引擎 — 会话注册、帧路由、级联管线（cascade）驱动。

级联管线（cascade 模式，默认）：麦克风帧 → 端点检测 → 流式 ASR 定稿
→ 用户消息经 AgentApp 统一入口进入思维（人格/记忆/工具全量生效，
与文字消息同一条大脑路径）→ event_bus 的回复增量 → TTS 管线 →
播放队列 → 下行音频帧。回复文本同时走既有频道事件流（聊天记录/WS
delta）——语音只是回复的第二种呈现，不产生第二条对话路径。

仲裁纪律：同一 scope 的思维轮天然串行（Mind 的 scope 队列）；语音侧
只跟踪"当前期待回复的一轮"（_pending_scope + 起始 mind turn），barge-in
后旧轮增量不再送 TTS（文本照常在聊天记录呈现）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from agent.realtime.playback import PlaybackFrame
from agent.realtime.session import RealtimeSession, RealtimeSink, SessionState
from agent.voice.session import VoiceDelivery, VoiceLeaseBusy
from agent.voice.turn_detection import TurnEvent
from core.config import get_config_bool, get_config_int
from core.log import log

_LOG_TAG = "实时语音"


class RealtimeEngine:
    """实时语音引擎（进程内单例，经 get_realtime_engine 获取）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, RealtimeSession] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        """scope → 等待回复的会话与思维轮标记（TTS 增量的准入判定）。"""
        self._bus_hooked = False

    # ------------------------------------------------------------------
    # 会话生命周期
    # ------------------------------------------------------------------

    async def start(
        self,
        owner: str,
        delivery: VoiceDelivery,
        sink: RealtimeSink,
        sample_rate: int = 16000,
    ) -> RealtimeSession:
        """开启实时语音会话（同 owner 已有会话则显式拒绝）。"""
        if owner in self._sessions:
            raise VoiceLeaseBusy(f"已有进行中的实时语音会话（owner={owner}）")
        if not get_config_bool("realtime_enabled", True):
            raise RuntimeError("实时语音已停用（realtime_enabled）")
        from core.config import get_config
        mode = str(get_config("realtime_mode", "cascade") or "cascade").lower()
        if mode != "native":
            await self._check_cascade_ready(sink)
        session = RealtimeSession(
            owner=owner, delivery=delivery, sample_rate=sample_rate, sink=sink)
        session.start_writer(get_config_int("realtime_playback_rate", 48000))
        self._sessions[owner] = session
        if mode == "native":
            try:
                await self._start_native(session)
            except Exception:
                # 连接失败不留半截会话（否则重试撞租约）
                self._sessions.pop(owner, None)
                await session.close()
                raise
        else:
            self._hook_event_bus()
        # 初始状态帧：连接建立即知会话状态（set_state 同态早退不覆盖此帧）
        await session.sink.send_event("rt_state", {
            "state": SessionState.LISTENING.value, "turn_id": session.turn_id,
        })
        log(f"实时语音会话开始: owner={owner} rate={sample_rate} mode={mode}",
            "DEBUG", tag=_LOG_TAG)
        return session

    async def stop(self, owner: str) -> None:
        """结束实时语音会话（幂等）。"""
        session = self._sessions.pop(owner, None)
        if session is None:
            return
        self._pending.pop(self._scope_of(session), None)
        pump = getattr(session, "_native_pump_task", None)
        if pump is not None and not pump.done():
            pump.cancel()
        native = getattr(session, "_native_client", None)
        if native is not None:
            try:
                await native.close()
            except Exception:
                pass
        await session.close()
        log(f"实时语音会话结束: owner={owner}", "DEBUG", tag=_LOG_TAG)

    def owns(self, owner: str) -> bool:
        return owner in self._sessions

    def active_session(self) -> Optional[RealtimeSession]:
        """当前活动的语音会话（至多一条优先返回；无则 None）。"""
        return next(iter(self._sessions.values()), None)

    def status(self) -> Dict[str, Any]:
        from agent.voice.turn_detection import detector_status

        return {
            "sessions": len(self._sessions),
            "owners": sorted(self._sessions),
            "states": {o: s.state.value for o, s in self._sessions.items()},
            "endpoint": detector_status(),
        }

    async def _check_cascade_ready(self, sink: RealtimeSink) -> None:
        """级联模式启动门禁：无 ASR 拒绝（语音输入无法理解）；无 TTS 降级警告。"""
        from agent.audio import get_audio_registry
        from agent.audio.providers import KIND_ASR
        from agent.audio.streaming import KIND_ASR_STREAM
        has_asr = (await get_audio_registry().resolve(KIND_ASR_STREAM) is not None
                   or await get_audio_registry().resolve(KIND_ASR) is not None)
        if not has_asr:
            raise RuntimeError(
                "无可用 ASR 提供者：实时语音需要转写服务"
                "（在音源同步组件配置 FunASR，或切 realtime_mode=native）")
        from agent.tts import get_tts_registry
        if await get_tts_registry().resolve() is None:
            await sink.send_event("rt_error", {
                "level": "warn",
                "message": "未配置 TTS 提供者：回复将只有文字没有声音"
                           "（在 MiniMax 实体或模型配置中补齐 TTS 凭据，或安装 edge-tts）",
            })

    # ------------------------------------------------------------------
    # 原生模式（提供方直连音频通道）
    # ------------------------------------------------------------------

    async def _start_native(self, session: RealtimeSession) -> None:
        """建立提供方原生实时通道并启动事件泵。"""
        from agent.realtime.native import create_native_client
        from agent.realtime.playback import PcmResampler
        from core.config import get_config
        provider = str(get_config("realtime_native_provider", "openai") or "openai")
        client = create_native_client(
            provider,
            voice=str(get_config("realtime_tts_voice", "") or ""),
            instructions=self._native_instructions(),
        )
        await client.connect()
        session._native_client = client
        if client.input_rate != session.sample_rate:
            session._native_resampler = PcmResampler(
                session.sample_rate, client.input_rate)
        session._native_pump_task = asyncio.create_task(
            self._native_pump(session, client), name=f"rt.native.pump.{session.owner}")

    def _native_instructions(self) -> str:
        """原生模式的系统指令：配置优先，缺省从人格档案组装（语音对话风格约束）。"""
        from core.config import get_config
        configured = str(get_config("realtime_native_instructions", "") or "").strip()
        if configured:
            return configured
        try:
            from agent.runtime.factory import load_persona
            char = load_persona()
            name = str(getattr(char, "name", "") or "").strip()
            persona = str(getattr(char, "system_prompt", "") or "").strip()[:600]
        except Exception:
            name, persona = "", ""
        parts = [p for p in (
            f"你是{name}。" if name else "",
            persona,
            "正在进行实时语音对话：回复简短口语化，一次不超过三句话；"
            "不要用 markdown、列表、代码块或表情符号——你说的每句话都会被朗读出来。",
        ) if p]
        return "\n".join(parts)

    async def _native_pump(self, session: RealtimeSession, client: Any) -> None:
        """原生通道事件 → 播放队列 / 状态机 / 转写展示。"""
        from agent.realtime.playback import PlaybackFrame
        try:
            async for event in client.events():
                if session.closed:
                    return
                if event.kind == "audio":
                    if session.state is SessionState.LISTENING:
                        session.next_turn()
                    await session.set_state(SessionState.SPEAKING)
                    session.playback.push(PlaybackFrame(
                        pcm=event.pcm, sample_rate=event.sample_rate,
                        turn_id=session.turn_id))
                elif event.kind == "speech_started":
                    await client.interrupt()
                    await session.interrupt()
                elif event.kind == "transcript":
                    await session.sink.send_event(
                        "rt_final" if event.final else "rt_partial",
                        {"text": event.text, "role": event.role,
                         "turn_id": session.turn_id})
                elif event.kind == "turn_complete":
                    session.playback.finish(session.turn_id)
                elif event.kind == "error":
                    log(f"原生通道错误 [{session.owner}]: {event.message}",
                        "WARNING", tag=_LOG_TAG)
                    await session.sink.send_event("rt_error", {
                        "level": "error",
                        "message": f"语音通道中断: {event.message[:120]}",
                    })
                    await self.stop(session.owner)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"原生事件泵异常: {exc}", "WARNING", tag=_LOG_TAG)

    # ------------------------------------------------------------------
    # 帧路由（麦克风 → 端点检测 → ASR）
    # ------------------------------------------------------------------

    async def accept_pcm(self, owner: str, pcm: bytes) -> None:
        """接收一帧麦克风 PCM16（无会话的帧静默丢弃）。"""
        session = self._sessions.get(owner)
        if session is None or session.closed:
            return
        native = getattr(session, "_native_client", None)
        if native is not None:
            resampler = getattr(session, "_native_resampler", None)
            if resampler is not None:
                pcm = resampler.feed(pcm)
                if not pcm:
                    return
            await native.send_audio(pcm)
            return
        pcm = session.preprocessor.feed(pcm)
        if not pcm:
            return  # 预处理链内部缓冲未凑满一帧
        event = session.detector.accept_pcm(pcm)
        if event is TurnEvent.SPEECH_START:
            await self._on_speech_start(session)
        if session.detector.in_speech or session.asr_session is not None:
            await self._feed_asr(session, pcm)
        if event is TurnEvent.SPEECH_END:
            await self._on_speech_end(session)

    async def _on_speech_start(self, session: RealtimeSession) -> None:
        """语音起始：新用户轮开始（turn_id+1）；播放/思考中的打断确认。

        回声防护：她说话/思考中听到的语音不立即打断——先持续观察
        realtime_barge_in_onset_ms，仍是语音才打断（扬声器回声多为
        短促碎响，持续开口才是真打断）；确认前收束的按回声丢弃。
        """
        if session.state is not SessionState.LISTENING:
            if not get_config_bool("realtime_barge_in", True):
                return
            self._arm_barge_in_check(session)
            return
        session.next_turn()
        if session.asr_session is None:
            session.asr_session = await self._open_asr(session)

    def _arm_barge_in_check(self, session: RealtimeSession) -> None:
        """排定打断确认（在途确认不重复排；确认窗口内收束由 _on_speech_end 取消）。"""
        if session.barge_in_task is not None and not session.barge_in_task.done():
            return
        onset_ms = get_config_int("realtime_barge_in_onset_ms", 250)

        async def _confirm() -> None:
            try:
                await asyncio.sleep(onset_ms / 1000)
                if session.closed or not session.detector.in_speech:
                    return
                await self._barge_in(session)
                session.next_turn()
                if session.asr_session is None:
                    session.asr_session = await self._open_asr(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"打断确认异常: {exc}", "DEBUG", tag=_LOG_TAG)

        session.barge_in_task = asyncio.create_task(
            _confirm(), name=f"rt.bargein.{session.owner}")

    async def _barge_in(self, session: RealtimeSession) -> None:
        """打断当前轮：TTS 取消、播放清空、旧思维轮增量不再送 TTS。"""
        scope = self._scope_of(session)
        pending = self._pending.pop(scope, None)
        if pending and pending.get("session") is session:
            pending["superseded"] = True
            self._pending[scope] = pending
        await session.interrupt()
        log(f"barge-in: owner={session.owner} → turn {session.turn_id}",
            "DEBUG", tag=_LOG_TAG)

    async def _open_asr(self, session: RealtimeSession) -> Any:
        """开启流式 ASR 会话；无流式提供者退化为整段缓冲（定稿时整段转写）。"""
        from agent.audio import get_audio_registry
        from agent.audio.streaming import KIND_ASR_STREAM
        provider = await get_audio_registry().resolve(KIND_ASR_STREAM)
        if provider is not None:
            return provider.open_session(session.sample_rate)
        return None  # pcm_buffer 兜底路径

    async def _feed_asr(self, session: RealtimeSession, pcm: bytes) -> None:
        """语音帧送 ASR：partial 事件转发展示，原始音频留存兜底。"""
        session.pcm_buffer.extend(pcm)
        if session.asr_session is not None:
            try:
                events = await session.asr_session.accept_pcm(pcm, session.sample_rate)
            except Exception as exc:
                log(f"流式 ASR 帧处理失败: {exc}", "DEBUG", tag=_LOG_TAG)
                return
            for event in events:
                if event.kind == "partial" and event.text:
                    await session.sink.send_event("rt_partial", {
                        "text": event.text, "turn_id": session.turn_id,
                    })

    async def _on_speech_end(self, session: RealtimeSession) -> None:
        """语音收束：ASR 定稿 → 用户轮次（打断确认窗口内收束的按回声丢弃）。"""
        if session.barge_in_task is not None and not session.barge_in_task.done():
            # 打断确认窗口内就收束了——短促碎响（扬声器回声），丢弃不成轮
            session.barge_in_task.cancel()
            session.barge_in_task = None
            session.pcm_buffer.clear()
            return
        transcript = ""
        segments: List[Dict[str, Any]] = []
        if session.asr_session is not None:
            try:
                events = await session.asr_session.commit()
            except Exception as exc:
                log(f"流式 ASR 定稿失败（降级整段）: {exc}", "DEBUG", tag=_LOG_TAG)
                events = []
            for event in events:
                if event.kind == "final":
                    transcript = event.text
                    segments = event.segments
            session.asr_session = None
        # 冲刷预处理链的滞留样本（整段兜底与声纹识别吃到完整音频）
        tail = session.preprocessor.flush()
        if tail:
            session.pcm_buffer.extend(tail)
        if not transcript and session.pcm_buffer:
            transcript, segments = await self._whole_transcribe(
                bytes(session.pcm_buffer), session.sample_rate)
        transcript = transcript.strip()
        if not transcript:
            # 未识别到有效语音：显式收帧（客户端清部分转写），状态保持收听
            session.pcm_buffer.clear()
            await session.sink.send_event("rt_final", {
                "text": "", "turn_id": session.turn_id, "discarded": True,
            })
            return
        # 说话人只读识别（仅有效语音轮；标注谁在说话，应对由 AI 决定）
        speaker = await self._identify_speaker(bytes(session.pcm_buffer), session.sample_rate)
        session.pcm_buffer.clear()
        await session.sink.send_event("rt_final", {
            "text": transcript, "turn_id": session.turn_id,
        })
        await self._broadcast_transcript(session, transcript)
        await self.user_turn(session, transcript, segments, speaker)

    @staticmethod
    async def _broadcast_transcript(session: RealtimeSession, text: str) -> None:
        """用户语音转写定稿 → 频道聊天流（语音轮在消息流双向呈现）。"""
        if session.delivery.adapter_key != "webui" or not text.strip():
            return
        try:
            from core.event_bus import EVENT_CHAT_BROADCAST, event_bus

            await event_bus.emit(EVENT_CHAT_BROADCAST, {
                "event": "voice_transcript",
                "role": "user",
                "content": text,
                "chat_id": session.delivery.session_id,
            })
        except Exception:
            pass

    async def _identify_speaker(
        self, pcm: bytes, sample_rate: int,
    ) -> Optional[Dict[str, Any]]:
        """声纹只读识别：返回本段说话人简报（命中已知人）或 None。

        仅做向量提取与匹配查询，不建档不累积样本（入库由轮次末尾的
        ingest 统一负责）；无声纹提供者/库中无人/识别失败均返回 None
        （标注缺失不阻塞语音轮）。
        """
        from core.config import get_config_bool
        if not pcm or not get_config_bool("realtime_speaker_annotate", True):
            return None
        wav_path = ""
        try:
            from agent.audio import get_audio_store
            from agent.audio.matcher import match_vector
            from agent.audio.service import get_audio_service

            wav_path = await self._pcm_temp_wav(pcm, sample_rate)
            vector = await get_audio_service().speaker_embed(wav_path)
            if not vector:
                return None
            candidates = await match_vector(get_audio_store(), vector)
        except Exception as exc:
            log(f"说话人识别失败（跳过标注）: {exc}", "DEBUG", tag=_LOG_TAG)
            return None
        finally:
            if wav_path:
                from asyncio import to_thread
                from os import unlink
                try:
                    await to_thread(unlink, wav_path)
                except OSError:
                    pass
        best = candidates[0] if candidates else None
        if not best or not best.get("matched"):
            return {"name": "", "similarity": 0.0}
        return {
            "name": str(best.get("name") or best.get("speaker_key") or ""),
            "similarity": round(float(best.get("similarity", 0.0)), 2),
        }

    async def _pcm_temp_wav(self, pcm: bytes, sample_rate: int) -> str:
        """PCM 写临时 WAV（声纹提取需要文件输入；用后即删）。"""
        import asyncio
        import os
        import tempfile
        import wave

        def _write() -> str:
            fd, path = tempfile.mkstemp(prefix="rt_spk_", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                with wave.open(f, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm)
            return path

        wav_path = await asyncio.to_thread(_write)
        return wav_path

    async def _whole_transcribe(
        self, pcm: bytes, sample_rate: int,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """整段 ASR 兜底（无流式提供者时）：PCM → 临时 WAV → 整段转写。"""
        import asyncio
        import os
        import tempfile
        import wave

        def _write() -> str:
            fd, path = tempfile.mkstemp(prefix="rt_asr_", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                with wave.open(f, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm)
            return path

        try:
            wav_path = await asyncio.to_thread(_write)
        except Exception as exc:
            log(f"整段 ASR 临时文件失败: {exc}", "DEBUG", tag=_LOG_TAG)
            return "", []
        try:
            from agent.audio import get_audio_service
            segments = await get_audio_service().transcribe(wav_path)
            text = " ".join(s["text"].strip() for s in segments if s["text"].strip())
            return text, segments
        except Exception as exc:
            log(f"整段 ASR 失败: {exc}", "DEBUG", tag=_LOG_TAG)
            return "", []
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 用户轮次（统一入口进思维 + TTS 回复）
    # ------------------------------------------------------------------

    async def user_turn(
        self,
        session: RealtimeSession,
        transcript: str,
        segments: Optional[List[Dict[str, Any]]] = None,
        speaker: Optional[Dict[str, Any]] = None,
    ) -> None:
        """一段定稿语音 → 用户消息经统一入口进思维，等待增量回复喂 TTS。

        speaker 非空时消息附说话人标注（谁在说话的事实陈述，识别建档
        与应对方式均由 AI 与音频库自行决定，不做门控）。
        """
        turn_id = session.turn_id
        await session.set_state(SessionState.THINKING)
        scope = self._scope_of(session)
        self._pending[scope] = {
            "session": session, "turn_id": turn_id,
            "mind_turn": None, "superseded": False,
        }
        delivery = session.delivery
        content = transcript
        if speaker:
            tag = (f"[语音 说话人:{speaker['name']} 置信:{speaker['similarity']}]"
                   if speaker.get("name")
                   else "[语音 说话人:未注册]")
            content = f"{tag} {transcript}"
        try:
            from agent.runtime.agent_app import get_agent_app
            await get_agent_app().send_message(
                user_id=delivery.user_id,
                content=content,
                user_name=delivery.user_name,
                to_me=True,
                adapter_key=delivery.adapter_key,
                session_id=delivery.session_id,
            )
        except Exception as exc:
            self._pending.pop(scope, None)
            log(f"语音用户消息投递失败: {exc}", "WARNING", tag=_LOG_TAG)
            await session.sink.send_event("rt_error", {
                "level": "error",
                "message": "这轮语音没能送进思维（运行时未就绪），请再说一次",
            })
            await session.set_state(SessionState.LISTENING)
            return
        log(f"语音用户轮: turn={turn_id} text={transcript[:40]!r}", "DEBUG", tag=_LOG_TAG)
        # 音频解析产物入核心库（fail-open，不影响对话）
        if segments:
            try:
                from agent.audio import get_audio_service
                from agent.audio.schemas import IngestPayload, SegmentIn
                await get_audio_service().ingest_payload(IngestPayload(
                    source_file=f"realtime:{session.owner}",
                    device_source="realtime",
                    segments=[SegmentIn(
                        start_ms=int(s.get("start_ms", 0)),
                        end_ms=int(s.get("end_ms", 0)),
                        text=str(s.get("text", "")),
                        vector=s.get("vector"),
                    ) for s in segments],
                ))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 回复增量 → TTS（event_bus 监听，全局一次挂接）
    # ------------------------------------------------------------------

    def _hook_event_bus(self) -> None:
        if self._bus_hooked:
            return
        from core.event_bus import EVENT_AFTER_REPLY, event_bus
        from core.stream_events import EVENT_ASSISTANT_DELTA
        event_bus.on(EVENT_ASSISTANT_DELTA, self._on_delta, owner="realtime")
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply, owner="realtime")
        self._bus_hooked = True

    def _scope_of(self, session: RealtimeSession) -> str:
        """会话对应的思维 scope（webui 用户域；多会话 chat_id 后缀）。

        与 Everything 的 scope 构建同一约定：session_id 与 user_id 相同
        时不加后缀（历史默认会话 scope=user_{uid}）。
        """
        delivery = session.delivery
        base = f"user_{delivery.adapter_key}:{delivery.user_id}"
        if delivery.session_id and delivery.session_id != delivery.user_id:
            base = f"{base}#{delivery.session_id}"
        return base

    def session_for_scope(self, scope: str) -> Optional[RealtimeSession]:
        """按思维 scope 找通话会话（频道消息自动语音路由的挂接点）。"""
        for session in self._sessions.values():
            if self._scope_of(session) == scope:
                return session
        return None

    async def speak_to_scope(self, scope: str, text: str, voice: str = "") -> Dict[str, Any]:
        """把一段文本播给 scope 对应的通话会话（send_message 自动语音路由）。

        状态感知：用户说话中不插播（消息以文字送达，返回 spoken=False）；
        她正在说话则追加到播放队列；空闲/思考中则开新播报。文字消息本身
        已由频道层送达，这里只负责"同时说出来"。
        """
        session = self.session_for_scope(scope)
        if session is None or session.closed or not text.strip():
            return {"spoken": False, "reason": "no-session"}
        if session.detector.in_speech:
            return {"spoken": False, "reason": "user-speaking"}

        from core.config import get_config as _gc

        resolved = voice.strip() or str(_gc("realtime_tts_voice", "") or "").strip()
        turn_id = session.turn_id
        appending = session.state is SessionState.SPEAKING
        if not appending:
            await session.set_state(SessionState.SPEAKING)

        from agent.tts import TtsPipeline

        from .playback import PlaybackFrame
        pipeline = TtsPipeline(voice=resolved, sample_rate=24000)
        pipeline.feed(text.strip())
        pipeline.finish()
        session.last_say_error = ""

        async def _run() -> None:
            finished = False
            try:
                produced = 0
                async for rate, chunk in pipeline.stream():
                    if session.closed or session.turn_id != turn_id:
                        pipeline.cancel()
                        return
                    produced += 1
                    session.playback.push(PlaybackFrame(
                        pcm=chunk, sample_rate=rate, turn_id=turn_id))
                if produced == 0:
                    session.last_say_error = "全部 TTS 提供者不可用"
                    await session.sink.send_event("rt_error", {
                        "level": "warn",
                        "message": "语音合成失败（全部 TTS 提供者不可用）：这段话没能说出口",
                    })
                    return
                session.playback.finish(turn_id)
                finished = True
            finally:
                if not finished and not session.closed \
                        and session.state is SessionState.SPEAKING:
                    await session.set_state(SessionState.LISTENING)

        # 追加播报共用她当前的播报任务生命周期：取消旧任务、由新任务接管队列
        if session.tts_task is not None and not session.tts_task.done() and appending:
            session.tts_task.cancel()
        session.tts_task = asyncio.create_task(_run(), name=f"rt.speak.{session.owner}")
        return {"spoken": True, "appending": appending, "turn_id": turn_id}

    async def _on_delta(self, payload: Dict[str, Any]) -> None:
        scope = str(payload.get("scope", ""))
        pending = self._pending.get(scope)
        if not pending or pending.get("superseded"):
            return
        delta = str(payload.get("delta", ""))
        if not delta or payload.get("reasoning"):
            return
        session: RealtimeSession = pending["session"]
        if session.closed:
            return
        mind_turn = payload.get("turn_id")
        if pending["mind_turn"] is None:
            pending["mind_turn"] = mind_turn
            # 管线就地创建：首个增量立即入管（随后 stream 任务驱动合成）
            from agent.tts import TtsPipeline
            from core.config import get_config
            session.tts_pipeline = TtsPipeline(
                voice=str(get_config("realtime_tts_voice", "") or ""),
                sample_rate=24000)
            session.tts_task = asyncio.create_task(
                self._run_tts(session), name=f"rt.tts.{session.owner}")
            await session.set_state(SessionState.SPEAKING)
        elif pending["mind_turn"] != mind_turn:
            # 新一轮思维轮（如工具调用后的续写）：并入同一语音回复流
            pending["mind_turn"] = mind_turn
        if session.tts_pipeline is not None:
            session.tts_pipeline.feed(delta)

    async def _on_after_reply(self, payload: Dict[str, Any]) -> None:
        scope = str(payload.get("scope", ""))
        pending = self._pending.pop(scope, None)
        if not pending or pending.get("superseded"):
            return
        session: RealtimeSession = pending["session"]
        if payload.get("error") and not session.closed:
            await session.sink.send_event("rt_error", {
                "level": "warn", "message": "这轮回复出错了，可以再说一次",
            })
        if session.tts_pipeline is not None:
            session.tts_pipeline.finish()
        elif session.state is SessionState.THINKING:
            # 纯工具轮/空回复（无任何增量文本）：未开声即收尾
            await session.set_state(SessionState.LISTENING)

    async def _run_tts(self, session: RealtimeSession) -> None:
        """TTS 管线驱动：增量文本 → 句级合成 → 播放队列。"""
        pipeline = session.tts_pipeline
        if pipeline is None:
            return
        turn_id = session.turn_id
        produced = 0
        try:
            async for rate, chunk in pipeline.stream():
                if session.closed or session.turn_id != turn_id:
                    pipeline.cancel()
                    return
                produced += 1
                session.playback.push(PlaybackFrame(
                    pcm=chunk, sample_rate=rate, turn_id=turn_id))
            if produced == 0 and not session.closed:
                await session.sink.send_event("rt_error", {
                    "level": "warn",
                    "message": "语音合成失败（全部 TTS 提供者不可用）：本轮回复只有文字",
                })
            session.playback.finish(turn_id)
        except asyncio.CancelledError:
            pipeline.cancel()
            raise
        except Exception as exc:
            log(f"TTS 管线异常: {exc}", "WARNING", tag=_LOG_TAG)
            session.playback.finish(turn_id)


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_engine: Optional[RealtimeEngine] = None


def get_realtime_engine() -> RealtimeEngine:
    """进程内单例。"""
    global _engine
    if _engine is None:
        _engine = RealtimeEngine()
    return _engine
