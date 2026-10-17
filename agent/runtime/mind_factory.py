"""思维运行时组合根；完成全部接线后才向调用方返回宿主。"""

from __future__ import annotations

from agent.channel.manager import ChannelManager
from agent.context.assembly import ContextAssembly
from agent.context.compressor import ContextCompressor
from agent.heartbeat.engine import HeartbeatEngine
from agent.llm import ChatModel
from agent.llm.llm_manager import LLMManager
from agent.memory.embedding import get_embedder
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import CharacterAgent
from agent.mind.mind import Mind
from agent.mind.mind_tools import register_core_tools
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.skills import get_skill_store
from agent.storage.data_center import ConversationData, EverythingData
from agent.storage.storage_router import StorageRouter
from core.log import log
from core.plugins import get_plugin_manager
from core.tool_registry import activate_group


def create_mind(
    *, char: CharacterAgent, llm: ChatModel, llm_manager: LLMManager | None,
    channel_manager: ChannelManager, everything_data: EverythingData,
    conversation_data: ConversationData, storage_router: StorageRouter | None,
    memory_store: MemoryStore | None,
) -> Mind:
    """共享技能库与上下文视图，按依赖顺序装配思维服务。"""
    skill_store = get_skill_store()
    plugin_registry = get_plugin_manager().registry
    pfc = PrefrontalCortex(everything_data, channel_manager, conversation_data)
    context = ContextAssembly(
        pfc.work_memory, pfc.tool_assembly, channel_manager, conversation_data,
        skill_store, plugin_registry,
    )
    embedder = get_embedder("text")
    retriever = MemoryRetriever(memory_store, embedder) if memory_store else None
    if retriever is not None and llm_manager is not None:
        rerank_client = llm_manager.get_media_client("rerank")
        if rerank_client:
            retriever.set_rerank_client(rerank_client)
    mind = Mind(
        char=char, llm=llm, llm_manager=llm_manager, channel_manager=channel_manager,
        everything_data=everything_data, conversation_data=conversation_data,
        prefrontal_cortex=pfc, context=context, skill_store=skill_store,
        embedder=embedder, retriever=retriever, storage_router=storage_router,
        memory_store=memory_store,
    )
    mind.heartbeat_engine = HeartbeatEngine(mind)
    mind.compressor = ContextCompressor(mind)

    # 技能自学习系统（匹配/事实索引/策展/后台评审，共享已建库实例）
    from agent.skills import (
        SkillCurator,
        SkillMatcher,
        SkillReviewer,
    )
    mind.skill_matcher = SkillMatcher(mind.skill_store, mind.embedder)
    mind.skill_curator = SkillCurator(mind.skill_store, mind.skill_matcher.index)
    mind.skill_reviewer = SkillReviewer(mind, mind.skill_store, index=mind.skill_matcher.index)
    if mind._skills_enabled():
        mind.skill_reviewer.start()

    # 子代理委托管理器（delegate_task 工具经 wiring 端口消费本实例）
    from agent.delegation.delegation_manager import DelegationManager
    mind.delegation_manager = DelegationManager(mind)
    count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
    log(f"🤖 子代理工具已注册 ({count} 个)", tag="委托")

    # 工作流引擎（journal 化 DAG 编排；workflow_* 工具经 wiring 端口消费本实例）
    import agent.workflow.tools  # noqa: F401  （deferred 工具注册）
    from agent.workflow.engine import WorkflowEngine
    mind.workflow_engine = WorkflowEngine(mind)
    count = activate_group("workflow", "工作流 - 可恢复的多步骤编排（断点续跑/门控重跑）")
    log(f"🧩 工作流工具已注册 ({count} 个)", tag="工作流")

    # 记忆自动捕获管线（心跳 tick / 关停兜底 / 压缩前抢跑共用单例）
    from agent.memory.auto_capture import AutoCapturePipeline
    mind.auto_capture_pipeline = AutoCapturePipeline(mind)

    register_core_tools()
    return mind
