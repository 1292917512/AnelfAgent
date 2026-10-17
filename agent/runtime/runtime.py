from __future__ import annotations

from dataclasses import dataclass, field

from agent.channel import ChannelManager, InputPipeline
from agent.config import MindConfig
from agent.delegation.delegation_manager import DelegationManager
from agent.llm import ChatModel
from agent.llm.llm_manager import LLMManager
from agent.memory.embedding import Embedder
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import CharacterAgent
from agent.mind.mind import Mind
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.runtime.assistant import AgentAssistant
from agent.skills.runtime import SkillRuntime
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
    memory_store: MemoryStore | None
    embedder: Embedder
    skills: SkillRuntime = field(init=False)
    retriever: MemoryRetriever | None = field(init=False)
    delegation_manager: DelegationManager = field(init=False)
    assistant: AgentAssistant = field(init=False)
    mind: Mind = field(init=False)
    pfc: PrefrontalCortex = field(init=False)
    workflow_engine: WorkflowEngine = field(init=False)
