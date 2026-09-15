"""实时语音 AI 工具面：会话状态查询、通话应答与主动语音输出。

- realtime_reply：通话轮的应答出口——把本轮回复直接说给通话另一头
  （TTS 合成播放），替代 send_message 发文字；说完即结束本轮。
- realtime_say：通话中的主动开口（提醒/补充/搭话），仲裁纪律与
  barge-in 对齐——用户说话/她正在说话/思考中时显式拒绝（retryable），
  绝不切断进行中的轮次。
- 音色自主：两工具均可指定 voice（音色 ID）；默认音色经
  realtime_tts_voice 配置，AI 可用 sound_config 热改自定声线。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from core.config import get_config
from core.log import log
from entities._sdk import ErrorCause, deferred_tool, error_from_exception, tool_error

from .engine import get_realtime_engine
from .session import SessionState

_LOG_TAG = "实时语音"


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _resolve_voice(voice: str) -> str:
    """音色解析：显式指定 → 实时默认音色 → 声音域默认。"""
    explicit = str(voice or "").strip()
    if explicit:
        return explicit
    return str(get_config("realtime_tts_voice", "") or "").strip()


async def _speak_to_session(session, text: str, voice: str) -> dict:
    """把一段文本经 TTS 播给通话会话（state 置 SPEAKING，播完回 LISTENING）。"""
    turn_id = session.turn_id
    await session.set_state(SessionState.SPEAKING)

    from agent.tts import TtsPipeline

    from .playback import PlaybackFrame
    pipeline = TtsPipeline(voice=voice, sample_rate=24000)
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

    session.tts_task = asyncio.create_task(_run(), name=f"rt.speak.{session.owner}")
    return {"speaking": True, "turn_id": turn_id}


@deferred_tool(group="voice", tags=["always"], concurrency_safe=True)
async def realtime_status() -> str:
    """查看实时语音通话状态：活跃会话数/各会话状态（收听/思考/说话）/模式与关键配置。"""
    try:
        engine = get_realtime_engine()
        session = engine.active_session()
        return _dump({
            **engine.status(),
            "mode": get_config("realtime_mode", "cascade"),
            "barge_in": get_config("realtime_barge_in", True),
            "enabled": get_config("realtime_enabled", True),
            "default_voice": _resolve_voice(""),
            "last_say_error": session.last_say_error if session else "",
            "note": "多连接同开通话时 realtime_say/realtime_reply 作用于最早建立的会话",
        })
    except Exception as e:
        return error_from_exception(e, action="查看实时语音状态")


@deferred_tool(group="voice", tags=["always"])
async def realtime_reply(text: str, voice: str = "", end_turn: bool = True) -> str:
    """语音通话中的本轮应答出口：把回复直接说给通话另一头（TTS 播放）。

    正在语音通话时优先用本工具应答，不要用 send_message 发文字——通话
    中的主人耳朵在电话上，看不到聊天框。说完本轮即结束（end_turn）。

    Args:
        text: 要说的话（自然口语；markdown/旁白自动剥离）
        voice: 可选音色 ID（留空用实时默认音色 realtime_tts_voice）
        end_turn: 说完即结束本轮（默认；置 False 可说完继续执行工具）
    """
    if not text.strip():
        return tool_error("text 不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        engine = get_realtime_engine()
        session = engine.active_session()
        if session is None:
            return tool_error(
                "当前没有进行中的实时通话", cause=ErrorCause.STATE, retryable=False,
                hint="非通话场景的回复用 send_message")
        if session.detector.in_speech:
            return tool_error(
                "用户正在说话", cause=ErrorCause.STATE, retryable=True,
                hint="等用户这轮说完再应答（可用 realtime_status 查看状态）")
        if session.state is not SessionState.LISTENING:
            return tool_error(
                f"她正在{('思考' if session.state is SessionState.THINKING else '说话')}",
                cause=ErrorCause.STATE, retryable=True,
                hint="当前回复正在播出；追加内容等本轮结束再说")
        out = await _speak_to_session(session, text, _resolve_voice(voice))
        log(f"通话应答: {text[:40]!r}", "DEBUG", tag=_LOG_TAG)
        if end_turn:
            import json as _json

            from core.entity import EntityRegistry

            await EntityRegistry.execute_tool(
                "end_reply",
                _json.dumps({"reason": f"通话应答已说出口（{len(text)} 字)"},
                            ensure_ascii=False))
        return _dump({"success": True, **out, "end_turn": end_turn})
    except Exception as e:
        return error_from_exception(e, action="通话应答")


@deferred_tool(group="voice", tags=["core"])
async def realtime_say(text: str, voice: str = "") -> str:
    """在实时通话中主动开口说一段话（提醒/补充/主动搭话）。

    仅当有进行中的实时通话且当前空闲（用户没在说话、她没在说话/思考）时
    可用——忙时返回可重试错误，请等用户轮次结束后再说。
    语义说明：提交即异步合成播放（success 表示已受理）；全部 TTS 提供者
    失败时用户端会看到提示且听不到这段话——需要确认时可再调
    realtime_status 看 last_say_error。
    本轮应答回复请用 realtime_reply（说给通话另一头），本工具用于
    轮次之外的主动开口。

    Args:
        text: 要说的话（会经朗读清洗：markdown/旁白自动剥离）
        voice: 可选音色 ID（留空用实时默认音色 realtime_tts_voice）
    """
    if not text.strip():
        return tool_error("text 不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        engine = get_realtime_engine()
        session = engine.active_session()
        if session is None:
            return tool_error(
                "当前没有进行中的实时通话", cause=ErrorCause.STATE, retryable=False,
                hint="用户在声音页发起通话后才可用 realtime_say 主动开口")
        if session.detector.in_speech:
            return tool_error(
                "用户正在说话", cause=ErrorCause.STATE, retryable=True,
                hint="等用户这轮说完再开口（可用 realtime_status 查看状态）")
        if session.state is not SessionState.LISTENING:
            return tool_error(
                f"她正在{('思考' if session.state is SessionState.THINKING else '说话')}",
                cause=ErrorCause.STATE, retryable=True,
                hint="等她说完后再主动开口（可用 realtime_status 查看状态）")
        out = await _speak_to_session(session, text, _resolve_voice(voice))
        log(f"主动语音: {text[:40]!r}", "DEBUG", tag=_LOG_TAG)
        return _dump({"success": True, **out})
    except Exception as e:
        return error_from_exception(e, action="主动语音")
