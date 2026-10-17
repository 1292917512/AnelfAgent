"""思维运行时组合根；完成全部接线后才向调用方返回宿主。"""

from __future__ import annotations

import asyncio
from functools import partial

from agent.channel.manager import ChannelManager
from agent.config import get_mind_config
from agent.context.assembly import ContextAssembly
from agent.context.compressor import ContextCompressor
from agent.heartbeat.engine import HeartbeatEngine
from agent.llm import ChatModel
from agent.llm.llm_manager import LLMManager
from agent.memory.embedding import get_embedder
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import CharacterAgent
from agent.mind.background_routing import on_task_alert, on_task_unclaimed
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.llm_invoker import ModelInvoker
from agent.mind.mind import Mind
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.push import PushHub
from agent.mind.tools.core import register_core_tools
from agent.skills import SkillCurator, SkillMatcher, SkillReviewer, get_skill_store
from agent.skills.runtime import SkillRuntime
from agent.storage.data_center import ConversationData, EverythingData
from core.config import get_config_bool
from core.log import log
from core.plugins import get_plugin_manager
from core.tool_registry import activate_group


def create_mind(
    *, char: CharacterAgent, llm: ChatModel, llm_manager: LLMManager | None,
    channel_manager: ChannelManager, everything_data: EverythingData,
    conversation_data: ConversationData,
    memory_store: MemoryStore | None,
) -> Mind:
    """在运行中的事件循环内装配思维服务，完成接线后返回宿主。"""
    loop = asyncio.get_running_loop()
    skill_store = get_skill_store()
    plugin_registry = get_plugin_manager().registry
    pfc = PrefrontalCortex(everything_data, channel_manager, conversation_data)
    context = ContextAssembly(
        pfc.work_memory, pfc.tool_assembly, channel_manager, conversation_data,
        skill_store, plugin_registry,
    )
    embedder = get_embedder("text")
    skill_matcher = SkillMatcher(skill_store, embedder)
    skills = SkillRuntime(skill_store, skill_matcher, SkillCurator(skill_store, skill_matcher.index))
    background_tasks = BackgroundTaskRegistry(loop=loop)
    retriever = MemoryRetriever(memory_store, embedder) if memory_store else None
    if retriever is not None and llm_manager is not None:
        rerank_client = llm_manager.get_media_client("rerank")
        if rerank_client:
            retriever.set_rerank_client(rerank_client)
    mind = Mind(
        config=get_mind_config(),
        char=char, llm=llm, llm_manager=llm_manager, channel_manager=channel_manager,
        everything_data=everything_data, conversation_data=conversation_data,
        prefrontal_cortex=pfc, context=context, skills=skills, background_tasks=background_tasks,
        embedder=embedder, retriever=retriever,
        memory_store=memory_store,
    )
    mind.model_invoker = ModelInvoker(mind)
    background_tasks.set_unclaimed_callback(partial(on_task_unclaimed, mind))
    background_tasks.set_alert_callback(partial(on_task_alert, mind))
    mind.push_hub = PushHub(pfc, mind.try_execute_mind, loop=loop)
    mind.heartbeat_engine = HeartbeatEngine(mind)
    mind.compressor = ContextCompressor(mind.model_invoker)

    reviewer = SkillReviewer(mind, skill_store, index=skill_matcher.index)
    if get_config_bool("skills_enabled", True):
        reviewer.start()

    # 子代理委托管理器（delegate_task 工具经 wiring 端口消费本实例）
    from agent.delegation.delegation_manager import DelegationManager
    mind.delegation_manager = DelegationManager(mind)
    count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
    log(f"🤖 子代理工具已注册 ({count} 个)", tag="委托")

    register_core_tools()
    return mind
