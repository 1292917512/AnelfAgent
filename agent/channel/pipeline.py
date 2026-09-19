"""输入处理管道 -- 替代 respond/input_senses/senses.py。

职责：消息消费者分发（AgentAssistant.feel()）。
"""

from __future__ import annotations

from typing import List, Protocol

from agent.messages import Everything
from core.log import log


class MessageConsumer(Protocol):
    """消息消费者协议（如 AgentAssistant）。"""

    async def feel(self, anything: Everything) -> None: ...


class InputPipeline:
    """输入处理管道：消费者分发。"""

    def __init__(self) -> None:
        self._consumers: List[MessageConsumer] = []

    def register_consumer(self, consumer: MessageConsumer) -> None:
        self._consumers.append(consumer)

    def register_agent(self, agent: MessageConsumer) -> None:
        self.register_consumer(agent)

    async def ingest(self, anything: Everything) -> None:
        """处理输入消息并分发给所有消费者。"""
        log(f"管道接收消息: {str(anything)[:80]}", "DEBUG", tag="通道")
        for consumer in self._consumers:
            await consumer.feel(anything)
