from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol

from agent.messages import Everything
from agent.respond.input_senses.built_in.sense_file import SensePath
from agent.respond.input_senses.sense_unit_interface import SenseUnit
from core.tags import etag_all


class FeelConsumer(Protocol):
    async def feel(self, anything: Everything) -> None:
        ...


class SenseOrgan:
    def __init__(self) -> None:
        self.senses_units: list[SenseUnit] = [SensePath()]

    def register_senses_unit(self, senses_unit: SenseUnit) -> None:
        self.senses_units.append(senses_unit)

    async def sensory_output(self, content: str) -> str:
        label_list = etag_all(content)
        for label in label_list:
            for senses_unit in self.senses_units:
                if label_content := await senses_unit.processing_sensory(label):
                    temp = f"[{label[0]}:{label[1]}]"
                    content = content.replace(temp, label_content)
        return content


class Senses:
    def __init__(self) -> None:
        self._consumers: list[FeelConsumer] = []
        self.senses_organ: SenseOrgan = SenseOrgan()

    def register_consumer(self, consumer: FeelConsumer) -> None:
        self._consumers.append(consumer)

    def register_agent(self, agent: FeelConsumer) -> None:
        # 向后兼容命名（旧代码叫 register_agent）
        self.register_consumer(agent)

    def register_senses_unit(self, senses_unit: SenseUnit) -> None:
        self.senses_organ.register_senses_unit(senses_unit)

    async def _push_to_consumers(self, anything: Everything) -> None:
        for consumer in self._consumers:
            await consumer.feel(anything)

    async def accept_data(self, anything: Everything) -> None:
        content: str = await self.senses_organ.sensory_output(str(anything))
        anything.set_text_content(content)
        await self._push_to_consumers(anything)

