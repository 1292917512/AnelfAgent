"""运行时工厂：加载人设，装配共享服务并完成思维宿主接线。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.log import log

if TYPE_CHECKING:
    from agent.messages import CharacterAgent
    from agent.mind.mind import Mind
    from agent.runtime.runtime import AgentRuntime


def load_persona() -> CharacterAgent:
    """从人设目录加载活跃人设。"""
    from agent.config import get_config_provider
    from agent.messages import CharacterAgent
    from core.tags import get_tag_desc

    provider = get_config_provider()
    persona_data = provider.get_persona_config()
    # 拷贝一份，避免就地修改配置提供者返回的共享 personality 列表
    prompts: list[str] = list(persona_data.get("personality", []))

    tag_prompt = f"消息中的 [key:value] 是元数据标签：{get_tag_desc()}回复正文中不要输出任何标签。"
    if tag_prompt not in prompts:
        prompts.append(tag_prompt)

    persona_name = persona_data.get("name", provider.get_active_persona_name() or "default")
    log(f"已加载人设: {persona_name} ({len(prompts)} 条提示词)")
    return CharacterAgent(personality=prompts)


def create_mind(runtime: AgentRuntime) -> Mind:
    """在运行中的事件循环内装配思维服务，完成接线后返回宿主。"""
    import asyncio
    from functools import partial

    from agent.context.assembly import ContextAssembly
    from agent.context.compressor import ContextCompressor
    from agent.heartbeat.engine import HeartbeatEngine
    from agent.memory.memory_retriever import MemoryRetriever
    from agent.mind.background.routing import on_task_alert, on_task_unclaimed
    from agent.mind.background.tasks import BackgroundTaskRegistry
    from agent.mind.llm_invoker import ModelInvoker
    from agent.mind.mind import Mind
    from agent.mind.prefrontal_cortex import PrefrontalCortex
    from agent.mind.push import PushHub
    from agent.mind.recall.inputs import Recollection
    from agent.mind.tool_assembly import ToolAssembly
    from agent.mind.tools.core import register_core_tools
    from agent.skills import SkillCurator, SkillMatcher, SkillReviewer, get_skill_store
    from agent.skills.runtime import SkillRuntime
    from core.config import get_config_bool
    from core.plugins import get_plugin_manager
    from core.tool_registry import activate_group

    loop = asyncio.get_running_loop()
    conversation_data = runtime.data_center.conversation_data
    everything_data = runtime.data_center.everything_data
    channel_manager = runtime.channel_manager
    memory_store, embedder = runtime.memory_store, runtime.embedder
    skill_store = get_skill_store()
    plugin_registry = get_plugin_manager().registry
    tool_assembly = ToolAssembly(channel_manager)
    pfc = PrefrontalCortex(everything_data, conversation_data, tool_assembly=tool_assembly)
    context = ContextAssembly(
        pfc, tool_assembly, channel_manager, conversation_data,
        skill_store, plugin_registry,
    )
    skill_matcher = SkillMatcher(skill_store, embedder)
    runtime.skills = SkillRuntime(skill_store, skill_matcher, SkillCurator(skill_store, skill_matcher.index))
    background_tasks = BackgroundTaskRegistry(loop=loop)
    retriever = MemoryRetriever(memory_store, embedder) if memory_store else None
    runtime.retriever = retriever
    if retriever is not None and runtime.llm_manager is not None:
        rerank_client = runtime.llm_manager.get_media_client("rerank")
        if rerank_client:
            retriever.set_rerank_client(rerank_client)
    mind = Mind(
        config=runtime.config,
        char=runtime.char, models=runtime, channel_manager=channel_manager,
        conversation_data=conversation_data,
        pfc=pfc, tool_assembly=tool_assembly,
        context=context,
        recollection=Recollection(
            config=runtime.config, pfc=pfc, conversation_data=conversation_data,
            memory_store=memory_store, embedder=embedder, retriever=retriever, skills=runtime.skills,
        ),
        background_tasks=background_tasks,
        embedder=embedder, retriever=retriever,
        memory_store=memory_store,
    )
    mind.model_invoker = ModelInvoker(runtime)
    background_tasks.set_unclaimed_callback(partial(on_task_unclaimed, mind))
    background_tasks.set_alert_callback(partial(on_task_alert, mind))
    mind.push_hub = PushHub(pfc, mind.driver.request_cycle, loop=loop)
    mind.heartbeat_engine = HeartbeatEngine(mind, everything_data, memory=runtime, skills=runtime.skills)
    mind.compressor = ContextCompressor(mind.model_invoker)

    reviewer = SkillReviewer(mind, skill_store, index=skill_matcher.index)
    if get_config_bool("skills_enabled", True):
        reviewer.start()

    import agent.delegation.delegate_tool  # noqa: F401
    from agent.delegation.delegation_manager import DelegationManager
    runtime.delegation_manager = DelegationManager(mind)
    count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
    log(f"🤖 子代理工具已注册 ({count} 个)", tag="委托")

    register_core_tools()
    return mind
