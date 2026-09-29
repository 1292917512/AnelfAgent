"""流式增量聚合 — ChatStreamDelta 累积为 ChatResult。

供 LLMManager 内部调用的流式通道使用（如折叠摘要）。与 llm_invoker 的
主对话聚合（on_delta 上报 / 事件发射 / 频道标签清洗）职责不同，本模块
只做纯累积：思考中不出结果的调用方只关心最终文本与用量。

Model Experience:
- 模型看到什么：无 prompt 层变化；仅把内部辅助调用从非流式改为流式通道。
- token 影响：流式本身不改变 token 用量（usage 经 stream_options.include_usage 同口径回传）。
- 缓存影响：内部调用为独立小请求，不共享主对话前缀，不触碰任何前缀层。
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, List, Optional

from agent.llm.types import ChatResult, ChatStreamDelta, ToolCall, UsageInfo


class StreamAggregator:
    """增量累积流式片段，build() 产出聚合 ChatResult（含 TTFT）。"""

    def __init__(self) -> None:
        self._content_parts: List[str] = []
        self._reasoning_parts: List[str] = []
        self._tool_calls: List[ToolCall] = []
        self._usage: Optional[UsageInfo] = None
        self._finish_reason: str = ""
        self._started = time.monotonic()
        self._first_delta_at: Optional[float] = None

    def feed(self, delta: ChatStreamDelta) -> None:
        """累积一个片段；首个片段同时锚定 TTFT。"""
        if self._first_delta_at is None:
            self._first_delta_at = time.monotonic()
        if delta.content:
            self._content_parts.append(delta.content)
        if delta.reasoning_content:
            self._reasoning_parts.append(delta.reasoning_content)
        if delta.tool_calls:
            self._tool_calls.extend(delta.tool_calls)
        if delta.usage is not None:
            self._usage = delta.usage
        if delta.finish_reason:
            self._finish_reason = delta.finish_reason

    def build(self, model: str = "") -> ChatResult:
        """产出聚合结果（raw 为 None：流式路径无原始响应体）。"""
        ttft_ms: Optional[float] = None
        if self._first_delta_at is not None:
            ttft_ms = (self._first_delta_at - self._started) * 1000
        return ChatResult(
            content="".join(self._content_parts),
            tool_calls=self._tool_calls,
            finish_reason=self._finish_reason,
            reasoning_content="".join(self._reasoning_parts),
            usage=self._usage,
            model=model,
            ttft_ms=ttft_ms,
        )


async def aggregate_with_idle_timeout(
    stream_gen: AsyncGenerator[ChatStreamDelta, None],
    *,
    idle_timeout: float,
    model: str = "",
    label: str = "",
    ceiling_mult: int = 20,
) -> ChatResult:
    """以每 chunk 空闲超时消费流式增量并聚合为 ChatResult。

    空闲窗口内完全无响应才判死；思考/正文增量都算活动——深度思考模型的
    长思考阶段随增量持续回传，不受整窗读超时约束。
    总时长天花板 = 空闲窗口 × ceiling_mult：防端点以"周期性吐字节"的方式
    吊流（每 chunk 都有活动但永不结束），此时空闲超时失效、上层回退链不推进。
    """
    aggregator = StreamAggregator()
    loop = asyncio.get_running_loop()
    overall_deadline = loop.time() + idle_timeout * ceiling_mult
    who = f" [{label}]" if label else ""
    try:
        while True:
            if loop.time() > overall_deadline:
                raise asyncio.TimeoutError(f"LLM{who} 流式总时长超限")
            try:
                delta = await asyncio.wait_for(
                    stream_gen.__anext__(), timeout=idle_timeout,
                )
            except StopAsyncIteration:
                break
            aggregator.feed(delta)
    finally:
        try:
            await stream_gen.aclose()
        except Exception:
            pass  # 生成器已在取消中收尾或关闭失败，底层流由 chat_stream finally 兜底
    return aggregator.build(model=model)
