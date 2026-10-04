"""百炼语音识别组件 — 非流式转写 + 实时流式识别。

- 非流式（kind=asr）：多模态生成端点整段转写（音频内联 data:base64 URI），
  输出与 FunASR 客户端同构的 segments（单段 [0, 时长]，无端点时间戳），
  无缝进入核心入库管线；
- 流式（kind=asr_stream）：Recognition 流式会话，SDK 回调线程经
  call_soon_threadsafe 桥入事件循环，映射为 AsrEvent(partial/final)。

模型经 dashscope_asr_model / dashscope_stream_asr_model 配置；优先级
dashscope_asr_priority / dashscope_stream_asr_priority（默认 20：本地
FunASR 10 之后、内部模型链 50 之前）。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional

from core.config import ConfigManager
from core.log import log

from . import sdk

_LOG_TAG = "百炼"

# 多模态生成端点（音频 data URI 内联，与 agent.llm 的 DashScope ASR 适配器同协议）
_MULTIMODAL_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# 端点 data URI 上限 10MB，留 1MB 余量（16k 单声道 WAV 约 4.7 分钟）
_MAX_INLINE_BYTES = 9 * 1024 * 1024


def _wav_duration_ms(wav_path: str) -> int:
    """读 WAV 时长（毫秒）；读取失败返回 0（分段时间戳仅作展示与回听定位）。"""
    import wave

    try:
        with wave.open(wav_path, "rb") as wf:
            return int(wf.getnframes() * 1000 / max(1, wf.getframerate()))
    except Exception:
        return 0


def _extract_text(result: Dict[str, Any]) -> str:
    """从多模态生成响应提取转写文本（content 为字符串或 [{text}] 列表）。"""
    output = result.get("output")
    if not isinstance(output, dict):
        return ""
    choices = output.get("choices")
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict))
    return ""


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
        from entities._sdk import ensure_16k_mono_wav

        wav_path, converted = await ensure_16k_mono_wav(audio_path)
        cleanup = converted
        try:
            api_key = sdk.resolve_api_key()
            if not api_key:
                raise RuntimeError("未解析到阿里百炼 API Key（组件凭据 dashscope / 环境变量均无）")
            audio = await asyncio.to_thread(_read_bytes, wav_path)
            if len(audio) > _MAX_INLINE_BYTES:
                raise RuntimeError(
                    f"音频 {len(audio) // 1024 // 1024}MB 超内联上限（≈4.7 分钟），"
                    "请分段或改用本地 FunASR 转写")
            model = str(ConfigManager.get("dashscope_asr_model", "qwen-audio-3.0-asr-flash"))
            text = await _call_multimodal(model, api_key, audio)
            text = text.strip()
            if not text:
                return []
            return [{"start_ms": 0, "end_ms": _wav_duration_ms(wav_path), "text": text}]
        finally:
            if cleanup:
                import os

                try:
                    os.remove(wav_path)
                except OSError:
                    pass


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _call_multimodal(model: str, api_key: str, audio: bytes) -> str:
    """多模态生成端点整段转写；非 200 或响应异常抛错（供链式降级）。"""
    import httpx

    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {
                "data": f"data:audio/wav;base64,{base64.b64encode(audio).decode()}"}},
        ]}]},
        "parameters": {"format": "wav"},
    }
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(
            _MULTIMODAL_URL, json=payload,
            headers={"Authorization": f"Bearer {api_key}"})
    if resp.status_code != 200:
        raise RuntimeError(f"百炼转写返回 {resp.status_code}: {resp.text[:200]}")
    return _extract_text(resp.json())


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
            "dashscope_stream_asr_model", "qwen-audio-3.0-asr-flash-streaming"))
        return _DashScopeStreamSession(model, sample_rate)
