"""句级 TTS 流式管线 — 文本增量流 → 断句 → 预取合成 → 有序 PCM 块流。

数据流：
    LLM 增量文本流 → SentenceSplitter 断句 → 句队列
      → 预取窗口（tts_prefetch_sentences 句并行合成，当前句播放时
        后续句已在合成——首句延迟压到一句的合成耗时，后续零等待）
      → 句级失败降级（providers.synthesize_sentence 的优先级链）
      → 有序 PCM16 块输出（播放链逐块消费）

取消纪律：cancel() 终止全部在途合成任务并清空句队列（barge-in 打断时
播放链随之清空）；管线自身不持有事件循环资源，任务随 cancel 收束。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, List, Optional

from core.config import get_config_int
from core.log import log

from .providers import synthesize_sentence
from .sentences import SentenceSplitter, strip_for_speech

_LOG_TAG = "语音合成"


class TtsPipeline:
    """句级流式合成管线（一次对话轮一个实例，cancel 后可弃）。"""

    def __init__(self, *, voice: str = "", sample_rate: int = 24000) -> None:
        self._voice = voice
        self._sample_rate = sample_rate
        self._splitter = SentenceSplitter()
        self._sentences: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._tasks: List[asyncio.Task] = []
        self._cancelled = False

    def feed(self, delta: str) -> None:
        """推入 LLM 增量文本（经朗读清洗 + 断句入队）。"""
        if self._cancelled:
            return
        for sentence in self._splitter.feed(delta):
            cleaned = strip_for_speech(sentence)
            if cleaned:
                self._sentences.put_nowait(cleaned)

    def finish(self) -> None:
        """文本流收尾：残句入队并放置结束哨兵。"""
        if self._cancelled:
            self._sentences.put_nowait(None)
            return
        for sentence in self._splitter.flush():
            cleaned = strip_for_speech(sentence)
            if cleaned:
                self._sentences.put_nowait(cleaned)
        self._sentences.put_nowait(None)

    def is_cancelled(self) -> bool:
        """打断状态（外部 cancel 修改，读取走方法防调用点误判）。"""
        return self._cancelled

    def cancel(self) -> None:
        """打断：清空句队列、取消在途合成（已交付的音频块不受影响）。"""
        self._cancelled = True
        self._splitter.reset()
        while not self._sentences.empty():
            try:
                self._sentences.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._sentences.put_nowait(None)
        for task in self._tasks:
            task.cancel()

    async def stream(self) -> AsyncIterator[tuple[int, bytes]]:
        """有序产出 (sample_rate, pcm_chunk) 直到结束哨兵。"""
        prefetch = max(1, get_config_int("tts_prefetch_sentences", 2))
        pending: asyncio.Queue[Optional[tuple[asyncio.Task, int]]] = asyncio.Queue()
        chain: Optional[List[Any]] = None

        async def _produce() -> None:
            nonlocal chain
            while True:
                sentence = await self._sentences.get()
                if sentence is None:
                    await pending.put(None)
                    return
                if chain is None:
                    from .providers import get_tts_registry
                    chain = await get_tts_registry().available_chain()
                task = asyncio.create_task(
                    synthesize_sentence(
                        sentence, voice=self._voice,
                        sample_rate=self._sample_rate, chain=chain),
                    name="tts.sentence",
                )
                self._tasks.append(task)
                await pending.put((task, self._sample_rate))
                # 预取窗口：挂起中的合成任务不超过 prefetch 个
                while sum(1 for t in self._tasks if not t.done()) >= prefetch:
                    await asyncio.sleep(0.05)
                    if self._cancelled:
                        return

        producer = asyncio.create_task(_produce(), name="tts.produce")
        try:
            while True:
                item = await pending.get()
                if item is None:
                    return
                task, _rate = item
                if self._cancelled:
                    task.cancel()
                    continue
                try:
                    timeout = max(5.0, get_config_int("tts_sentence_timeout_s", 20))
                    stream = await asyncio.wait_for(task, timeout=timeout)
                except asyncio.TimeoutError:
                    log("句子合成超时（跳过该句，检查 TTS 提供者网络）",
                        "WARNING", tag=_LOG_TAG)
                    task.cancel()
                    continue
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    log(f"句子合成失败（跳过该句）: {exc}", "WARNING", tag=_LOG_TAG)
                    continue
                if stream is None:
                    log("全部 TTS 提供者失败，跳过一句", "WARNING", tag=_LOG_TAG)
                    continue
                async for chunk in stream.chunks:
                    if not self._cancelled:
                        yield stream.sample_rate, chunk
                if self.is_cancelled():
                    return
        finally:
            producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass
