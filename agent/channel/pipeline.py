"""输入处理管道 -- 替代 respond/input_senses/senses.py。

职责：tag 解析 + 消费者分发（AgentAssistant.feel()）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, List, Optional, Protocol

from agent.messages import Everything
from core.log import log


class MessageConsumer(Protocol):
    """消息消费者协议（如 AgentAssistant）。"""

    async def feel(self, anything: Everything) -> None: ...


class InputProcessor(ABC):
    """输入处理器基类。"""

    @abstractmethod
    async def process(self, anything: Everything) -> Everything: ...


class TagProcessor(InputProcessor):
    """Tag 解析处理器（[media_file:xxx] 等媒体标签占位替换）。"""

    async def process(self, anything: Everything) -> Everything:
        from core.tags import etag_all, media_file_tag

        # 只处理原始正文，避免把 [time]/[uid]/[channel] 等上下文标签写回正文。
        content = anything.get_text_content()
        for key, value in etag_all(content):
            if key != media_file_tag.tag_name:
                continue
            # TODO: 后续扩展为真实的文件路径/内容处理（读取文件、摘要等）
            content = content.replace(
                f"[{key}:{value}]", media_file_tag.generate_label("路径")
            )
        anything.set_text_content(content)
        return anything


class InputPipeline:
    """输入处理管道：处理器链 + 消费者分发。"""

    def __init__(self) -> None:
        self._processors: List[InputProcessor] = [TagProcessor()]
        self._consumers: List[MessageConsumer] = []

    def add_processor(self, processor: InputProcessor) -> None:
        self._processors.append(processor)

    def register_consumer(self, consumer: MessageConsumer) -> None:
        self._consumers.append(consumer)

    def register_agent(self, agent: MessageConsumer) -> None:
        self.register_consumer(agent)

    async def ingest(self, anything: Everything) -> None:
        """处理输入消息并分发给所有消费者。"""
        log(f"管道接收消息: {str(anything)[:80]}", "DEBUG", tag="通道")
        for processor in self._processors:
            anything = await processor.process(anything)
        for consumer in self._consumers:
            await consumer.feel(anything)
