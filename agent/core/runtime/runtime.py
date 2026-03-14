from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agent.core.channel import ChannelManager, InputPipeline
from agent.core.llm import ChatModel, LLMClient, get_llm_manager
from agent.core.messages import CharacterAgent
from agent.core.mind import Mind
from agent.core.runtime.assistant import AgentAssistant
from agent.core.storage.data_center import DataCenter
from core.log import log


@dataclass(slots=True)
class AgentRuntime:
    """将 ChannelManager + Mind + 存储 + LLM 等聚合为一个可运行整体。"""

    channel_manager: ChannelManager
    pipeline: InputPipeline
    assistant: AgentAssistant
    mind: Mind
    char: CharacterAgent
    llm: ChatModel
    data_center: DataCenter

    def switch_llm(self, new_llm: ChatModel) -> None:
        """热切换 LLM 实例，同步更新 runtime 和 mind 的引用。"""
        self.llm = new_llm
        self.mind.llm = new_llm
        log(f"LLM 已热切换: {new_llm.__class__.__name__}")

    def switch_llm_by_name(self, client_name: str) -> bool:
        """通过 LLMManager 中的客户端名称热切换 LLM。"""
        manager = get_llm_manager()
        client = manager.get_client(client_name)
        if client is None:
            log(f"LLM 客户端 '{client_name}' 不存在", "WARNING")
            return False
        self.switch_llm(client)
        return True

    # 向后兼容
    @property
    def respond(self):
        """兼容旧代码访问 respond。"""
        return _RespondCompat(self.channel_manager, self.pipeline)


class _RespondCompat:
    """向后兼容层：让旧代码通过 runtime.respond 访问新系统。"""

    def __init__(self, cm: ChannelManager, pipeline: InputPipeline) -> None:
        self._cm = cm
        self._pipeline = pipeline

    async def accept_data(self, anything):
        await self._pipeline.ingest(anything)

    @property
    def agent_action(self):
        return self._cm

    def register_output(self, output, adapter_key: str = ""):
        pass

    def register_agent(self, agent):
        self._pipeline.register_agent(agent)
