"""已废弃 -- 请使用 agent.channel 模块。

本模块保留仅为向后兼容，新代码不应依赖此模块。
"""

from __future__ import annotations

import warnings

from agent.messages import Everything
from agent.respond.input_senses.sense_unit_interface import SenseUnit
from agent.respond.input_senses.senses import Senses
from agent.respond.output_action.action import Action
from agent.respond.output_action.output_interface import OutputProtocol


class Respond:
    """
    输入/输出编排：\n
    - 输入：Senses（tag 解析 + 感知单元替换）\n
    - 输出：Action（按群/私聊分发到 OutputProtocol）\n
    """

    def __init__(self) -> None:
        self.agent_senses = Senses()
        self.agent_action = Action()

    def register_output(self, output: OutputProtocol, adapter_key: str = "") -> None:
        self.agent_action.register_output(output, adapter_key)

    def register_agent(self, agent) -> None:
        self.agent_senses.register_agent(agent)

    def register_senses_unit(self, senses_unit: SenseUnit) -> None:
        self.agent_senses.register_senses_unit(senses_unit)

    async def accept_data(self, anything: Everything) -> None:
        await self.agent_senses.accept_data(anything)


__all__ = ["Respond", "OutputProtocol"]

