from __future__ import annotations

from dataclasses import dataclass, field

from agent.channel import ChannelManager, InputPipeline
from agent.config import MindConfig
from agent.llm import ChatModel
from agent.llm.llm_manager import LLMManager
from agent.messages import CharacterAgent
from agent.mind.mind import Mind
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.runtime.assistant import AgentAssistant
from agent.storage.data_center import DataCenter
from agent.workflow.engine import WorkflowEngine


@dataclass(slots=True)
class AgentRuntime:
    """将 ChannelManager + Mind + 存储 + LLM 等聚合为一个可运行整体。"""

    channel_manager: ChannelManager
    pipeline: InputPipeline
    char: CharacterAgent
    config: MindConfig
    llm: ChatModel
    llm_manager: LLMManager | None
    data_center: DataCenter
    assistant: AgentAssistant = field(init=False)
    mind: Mind = field(init=False)
    pfc: PrefrontalCortex = field(init=False)
    workflow_engine: WorkflowEngine = field(init=False)
