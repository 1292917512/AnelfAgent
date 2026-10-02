"""Qwen 实时流式 ASR — 百炼 qwen3-asr-flash-realtime WebSocket 连接。

协议（与 OpenAI Realtime 同构，对齐 N.E.K.O asr_client/workers/qwen.py 的接线）：
- 连接：wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime，
  Bearer 鉴权（组件凭据中心 dashscope 条目 / 环境变量 DASHSCOPE_API_KEY）；
- 会话初始化（连接级一次）：modalities=["text"]、input_audio_format=pcm、
  sample_rate=16000、input_audio_transcription={}、turn_detection=None
  （manual 提交——轮次边界由本地端点检测器单一裁决，不做双 VAD 冲突）；
- 上行：input_audio_buffer.append（base64 PCM16 16k）；
- 下行：conversation.item.input_audio_transcription.text（partial：
  text=已确定 + stash=暂存）/ .completed（final：transcript）/ .failed；
- 定稿：本地端点收束时 commit() → input_audio_buffer.commit →
  等 completed 事件回传 final 文本（有界超时，失败抛给引擎降级整段 ASR）。

**一次语音一条连接**（会话随轮次生灭，commit 后即关）：跨轮共享连接时，
新一轮的首批帧会混入上一轮的待 commit 缓冲——定稿串字、幻影重复轮
（"本来是正确的变成了错误的"+同一句话被回复多遍）。建连耗时分摊到
消费任务（帧泵零阻塞，队列保序冲刷），单轮 ~300ms 换取轮间零串扰。
凭据缺失/连接失败时 check_available 为 False，链路自然落到 FunASR 滚动窗。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any, Dict, List, Optional

from core.config import get_config, get_config_bool
from core.log import log
from core.provider_keys import get_provider_key
from entities._sdk import AsrEvent, register_audio_provider

_LOG_TAG = "音源同步"

_MODEL = "qwen3-asr-flash-realtime"
_DEFAULT_WS_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
# commit 后等 transcription.completed 的上限（引擎侧另有 20s 兜底闸门）
_COMMIT_TIMEOUT_SECONDS = 15.0
# 连接失败后再次尝试的冷却（避免每轮语音都重连失败刷屏）
_RECONNECT_COOLDOWN_SECONDS = 30.0


def _ws_base() -> str:
    """Realtime WS 端点（realtime_qwen_asr_ws_base 可切 token-plan 等兼容端点）。"""
    return str(get_config("realtime_qwen_asr_ws_base", _DEFAULT_WS_BASE)
               or _DEFAULT_WS_BASE).strip() or _DEFAULT_WS_BASE


def _resolve_api_key(ws_base: str) -> str:
    """按端点归属解析凭据：llm_clients 同 host 供应商的 key（如 token-plan）
    → 组件凭据 dashscope → 环境变量 DASHSCOPE_API_KEY。"""
    from urllib.parse import urlparse
    host = urlparse(ws_base).hostname or ""
    if host:
        try:
            from entities._sdk import get_llm_manager
            manager = get_llm_manager()
            for provider in manager.list_providers():
                p_url = str(provider.get("base_url", "") or "")
                if urlparse(p_url).hostname == host and provider.get("api_key"):
                    return str(provider["api_key"]).strip()
        except Exception:
            pass  # 管理器未就绪时落到凭据中心
    return get_provider_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY", "").strip()


class _QwenAsrConnection:
    """一条 WS 连接（一轮语音专用）：reader 泵把转写事件路由给属主会话。"""

    def __init__(self, api_key: str, on_failure, ws_base: str = "") -> None:
        self._api_key = api_key
        self._ws_base = ws_base
        self._on_failure = on_failure
        self._ws: Any = None
        self._reader_task: Optional[asyncio.Task] = None
        self._owner: Optional["_QwenAsrSession"] = None
        self._ready = asyncio.Event()
        self._closed = False

    @property
    def alive(self) -> bool:
        return self._ws is not None and self._ready.is_set() and not self._closed

    async def ensure(self) -> None:
        """确保连接就绪（幂等）。"""
        if self.alive:
            return
        if self._closed:
            raise RuntimeError("连接已关闭")
        import websockets
        ws = None
        try:
            ws = await websockets.connect(
                f"{self._ws_base or _ws_base()}?model={_MODEL}",
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                # 百炼是公网直连可达端点：环境里的 SOCKS/HTTP 代理只会拦截
                # WS 升级（缺 python-socks 时直接失败），显式绕过
                proxy=None,
                # 收拢握手有界：远端不回 CLOSE 时默认 10s 阻塞会卡住
                # 轮末随关与 voice_end 链路（对齐 N.E.K.O 显式 close_timeout）
                close_timeout=1.0,
            )
            self._ws = ws
            self._ready.clear()
            self._reader_task = asyncio.create_task(
                self._reader(), name="asr.qwen.reader")
            await self._send({
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": 16000,
                    "input_audio_transcription": {},
                    "turn_detection": None,
                },
            })
            # 等 session.updated 确认（reader 置 ready）；超时视为连接失败
            await asyncio.wait_for(self._ready.wait(), timeout=10.0)
        except Exception:
            self._on_failure()
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            if self._ws is ws:
                self._ws = None
            raise

    async def _send(self, message: Dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(message))

    async def send_audio(self, pcm: bytes) -> None:
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode(),
        })

    async def commit(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    def attach(self, session: "_QwenAsrSession") -> None:
        self._owner = session

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._mark_dead(f"reader 异常: {exc}")

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        mtype = str(message.get("type", ""))
        if mtype == "session.updated":
            self._ready.set()
            return
        session = self._owner
        if mtype == "error":
            error = message.get("error") or {}
            log(f"qwen 实时 ASR 通道错误: {error.get('message', '') or error}",
                "DEBUG", tag=_LOG_TAG)
            if session is not None:
                session._on_error(str(error.get("message", "") or error))
            return
        if session is None:
            return
        if mtype == "conversation.item.input_audio_transcription.text":
            text = str(message.get("text", "")) + str(message.get("stash", ""))
            if text:
                session._on_partial(text)
        elif mtype == "conversation.item.input_audio_transcription.completed":
            session._on_final(str(message.get("transcript", "") or ""))
        elif mtype == "conversation.item.input_audio_transcription.failed":
            session._on_error(str(
                (message.get("error") or {}).get("message", "transcription failed")))

    def _mark_dead(self, reason: str) -> None:
        log(f"qwen 实时 ASR 连接断开: {reason}", "DEBUG", tag=_LOG_TAG)
        self._on_failure()
        self._ready.clear()
        self._ws = None
        if self._owner is not None:
            self._owner._on_error("连接断开")


class _QwenAsrSession:
    """一次连续语音的转写会话（独占一条 WS 连接，commit 后随轮关闭）。"""

    def __init__(self, conn: _QwenAsrConnection, sample_rate: int) -> None:
        self._conn = conn
        self._sample_rate = sample_rate
        self._pending_events: List[AsrEvent] = []
        self._commit_fut: Optional[asyncio.Future] = None
        self._failed: Optional[str] = None
        self._closed = False

    async def accept_pcm(self, pcm: bytes, sample_rate: int) -> List[AsrEvent]:
        if self._closed or self._failed:
            return self._drain()
        try:
            await self._conn.ensure()
            self._conn.attach(self)
            await self._conn.send_audio(pcm)
        except Exception as exc:
            # 连接级失败：本轮流式判死（partial 停流），引擎攒下的整段缓冲
            # 在 commit 时走整段兜底转写，语音轮不受影响
            self._failed = str(exc)
            log(f"qwen 实时 ASR 喂帧失败（本轮降级整段兜底）: {exc}", "DEBUG", tag=_LOG_TAG)
        return self._drain()

    async def commit(self) -> List[AsrEvent]:
        """本地端点收束触发服务端定稿：commit → 等 completed 回传 final。"""
        try:
            if self._failed:
                raise RuntimeError(f"qwen 实时 ASR 会话已失败: {self._failed}")
            if self._closed:
                return []
            self._closed = True
            await self._conn.ensure()
            loop = asyncio.get_running_loop()
            self._commit_fut = loop.create_future()
            try:
                await self._conn.commit()
                transcript = await asyncio.wait_for(
                    self._commit_fut, timeout=_COMMIT_TIMEOUT_SECONDS)
            finally:
                self._commit_fut = None
            text = (transcript or "").strip()
            return [AsrEvent(kind="final", text=text)] if text else []
        finally:
            # 一轮一连接：定稿（或失败）后随轮关闭，杜绝跨轮缓冲串扰
            await self._conn.close()

    async def close(self) -> List[AsrEvent]:
        if self._closed:
            await self._conn.close()
            return []
        try:
            return await self.commit()
        except Exception:
            await self._conn.close()
            return []

    # ------------------------------------------------------------------
    # reader 泵回调（事件循环线程）
    # ------------------------------------------------------------------

    def _drain(self) -> List[AsrEvent]:
        events = self._pending_events
        self._pending_events = []
        return events

    def _on_partial(self, text: str) -> None:
        self._pending_events.append(AsrEvent(kind="partial", text=text))

    def _on_final(self, transcript: str) -> None:
        if self._commit_fut is not None and not self._commit_fut.done():
            self._commit_fut.set_result(transcript)

    def _on_error(self, message: str) -> None:
        self._failed = message
        if self._commit_fut is not None and not self._commit_fut.done():
            self._commit_fut.set_exception(RuntimeError(message))


class QwenRealtimeAsrProvider:
    """Qwen 实时流式 ASR 提供者（asr_stream 链，优先于 FunASR 滚动窗）。"""

    name = "qwen_asr_realtime"
    kind = "asr_stream"
    priority = 5
    unavailable_hint = "未配置百炼凭据（组件凭据 dashscope / 环境变量 DASHSCOPE_API_KEY）或连接冷却中"

    def __init__(self) -> None:
        self._last_failure_at = 0.0

    def _note_failure(self) -> None:
        self._last_failure_at = time.monotonic()

    async def check_available(self) -> bool:
        if not get_config_bool("realtime_qwen_asr_enabled", True):
            return False
        if not _resolve_api_key(_ws_base()):
            return False
        # 连接失败冷却期内让位给链上的后续提供者（FunASR 滚动窗兜底）
        if self._last_failure_at and (
                time.monotonic() - self._last_failure_at < _RECONNECT_COOLDOWN_SECONDS):
            return False
        return True

    def open_session(self, sample_rate: int = 16000) -> _QwenAsrSession:
        ws_base = _ws_base()
        api_key = _resolve_api_key(ws_base)
        if not api_key:
            raise RuntimeError("无对应端点的凭据（组件凭据 dashscope / llm_clients 同 host 供应商 / 环境变量均无）")
        return _QwenAsrSession(_QwenAsrConnection(api_key, self._note_failure, ws_base), sample_rate)


def register_qwen_streaming_provider() -> None:
    """向核心音频注册表注册 Qwen 实时流式 ASR 组件（实体包导入时调用一次）。"""
    register_audio_provider(QwenRealtimeAsrProvider())
