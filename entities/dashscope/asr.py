"""百炼语音识别组件 — 非流式转写 + 实时流式识别。

- 非流式（kind=asr）：Recognition.call 整段转写，输出与 FunASR 客户端
  同构的 segments（start_ms/end_ms/text），无缝进入核心入库管线；
- 流式（kind=asr_stream）：Recognition 流式会话，SDK 回调线程经
  call_soon_threadsafe 桥入事件循环，映射为 AsrEvent(partial/final)。

模型经 dashscope_asr_model / dashscope_stream_asr_model 配置；优先级
dashscope_asr_priority / dashscope_stream_asr_priority（默认 20：本地
FunASR 10 之后、内部模型链 50 之前）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from core.config import ConfigManager
from core.log import log

from . import sdk

_LOG_TAG = "百炼"


class DashScopeAsrProvider:
    """非流式转写提供者（asr 类别）。"""

    name = "dashscope"
    kind = "asr"
    unavailable_hint = "百炼语音未就绪：未装 dashscope SDK 或未解析到 API Key"

    @property
    def priority(self) -> int:
        return int(ConfigManager.get("dashscope_asr_priority", 20) or 20)

    async def check_available(self) -> bool:
        return sdk.sdk_ready()

    async def transcribe(self, audio_path: str, source_time: str = "") -> List[Dict[str, Any]]:
        ds, reason = sdk.import_sdk()
        if ds is None:
            raise RuntimeError(reason)
        from entities._sdk import ensure_16k_mono_wav

        wav_path, converted = await ensure_16k_mono_wav(audio_path)
        cleanup = converted
        try:
            from dashscope.audio.asr import Recognition

            model = str(ConfigManager.get("dashscope_asr_model", "qwen-audio-3.0-asr-flash"))
            from typing import Any, cast

            recognition = Recognition(
                model=model, format="wav", sample_rate=16000,
                callback=cast(Any, None))
            result = await sdk.run_sync(recognition.call, wav_path)
            if str(result.status_code) != "200":
                raise RuntimeError(
                    f"百炼转写返回 {result.status_code}: {getattr(result, 'message', '')}")
            sentence = result.get_sentence()
            sentences = sentence if isinstance(sentence, list) else [sentence]
            segments: List[Dict[str, Any]] = []
            for s in sentences or []:
                if not s or not str(s.get("text", "")).strip():
                    continue
                segments.append({
                    "start_ms": int(s.get("begin_time") or 0),
                    "end_ms": int(s.get("end_time") or 0),
                    "text": str(s.get("text", "")).strip(),
                })
            return segments
        finally:
            if cleanup:
                import os

                try:
                    os.remove(wav_path)
                except OSError:
                    pass


class _Callback:
    """SDK 回调：识别事件桥入事件循环队列。"""

    def __init__(self, put) -> None:
        self._put = put

    def on_event(self, result) -> None:
        try:
            sentence = result.get_sentence()
        except Exception:
            return
        if not sentence or isinstance(sentence, list):
            return
        text = str(sentence.get("text", "")).strip()
        if not text:
            return
        from dashscope.audio.asr import RecognitionResult

        if RecognitionResult.is_sentence_end(sentence):
            self._put(("final", text))
        else:
            self._put(("partial", text))

    def on_error(self, result) -> None:
        self._put(("error", str(getattr(result, "message", "") or "识别异常")))

    def on_close(self) -> None:
        self._put(("closed", ""))

    def on_complete(self) -> None:
        self._put(("complete", ""))

    def on_open(self) -> None:
        pass


class _DashScopeStreamSession:
    """一次流式识别会话：PCM 帧上行 → partial/final 事件下行。"""

    def __init__(self, model: str, sample_rate: int) -> None:
        self._model = model
        self._sample_rate = sample_rate
        self._recognition: Any = None
        self._queue: Optional[asyncio.Queue] = None
        self._started = False
        self._finished = False

    async def _ensure_started(self) -> None:
        if self._started:
            return
        ds, reason = sdk.import_sdk()
        if ds is None:
            raise RuntimeError(reason)
        from dashscope.audio.asr import Recognition

        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        put = sdk.thread_to_loop(loop, self._queue)
        from typing import cast

        self._recognition = Recognition(
            model=self._model, format="pcm", sample_rate=self._sample_rate,
            callback=cast(Any, _Callback(put)))
        await sdk.run_sync(self._recognition.start)
        self._started = True

    async def accept_pcm(self, pcm: bytes, sample_rate: int) -> List[Any]:
        from entities._sdk import AsrEvent

        if self._finished or not pcm:
            return []
        await self._ensure_started()
        # SDK 的 WS 发送为短阻塞（~100ms 帧量级），线程化避免拖事件循环
        await sdk.run_sync(self._recognition.send_audio_frame, pcm)
        events: List[AsrEvent] = []
        queue = self._queue
        while queue is not None and not queue.empty():
            kind, text = queue.get_nowait()
            if kind == "final":
                events.append(AsrEvent(kind="final", text=text))
            elif kind == "partial":
                events.append(AsrEvent(kind="partial", text=text))
            elif kind == "error":
                log(f"百炼流式识别错误: {text}", "WARNING", tag=_LOG_TAG)
        return events

    async def close(self) -> List[Any]:
        from entities._sdk import AsrEvent

        if self._finished:
            return []
        self._finished = True
        events: List[AsrEvent] = []
        if self._recognition is not None and self._started:
            try:
                await sdk.run_sync(self._recognition.stop)
            except Exception as exc:
                log(f"百炼流式识别收尾异常: {exc}", "DEBUG", tag=_LOG_TAG)
        # stop 触发的尾批 final 事件入队后再收
        queue = self._queue
        if queue is not None:
            await asyncio.sleep(0)
            for _ in range(60):
                if queue.empty():
                    break
                kind, text = queue.get_nowait()
                if kind == "final" and text:
                    events.append(AsrEvent(kind="final", text=text))
        return events


class DashScopeStreamAsrProvider:
    """流式识别提供者（asr_stream 类别，实时通话级联链）。"""

    name = "dashscope"
    kind = "asr_stream"
    unavailable_hint = "百炼语音未就绪：未装 dashscope SDK 或未解析到 API Key"

    @property
    def priority(self) -> int:
        return int(ConfigManager.get("dashscope_stream_asr_priority", 20) or 20)

    async def check_available(self) -> bool:
        return sdk.sdk_ready()

    def open_session(self, sample_rate: int = 16000) -> _DashScopeStreamSession:
        model = str(ConfigManager.get(
            "dashscope_stream_asr_model", "fun-asr-realtime"))
        return _DashScopeStreamSession(model, sample_rate)
