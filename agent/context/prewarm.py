"""折叠后的上下文缓存预热；复用正式构建与发送规整路径。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agent.context.assembly import ContextAssembly
from agent.context.message_schema import normalize_for_send
from agent.llm import ChatModel
from agent.llm.llm_client import LLMClient
from agent.memory.memory_retriever import MemoryRetriever
from agent.messages import CharacterAgent
from agent.storage.data_center import ConversationData
from core.log import log


async def prewarm_scope_cache(
    scope_type: str, scope_id: str, *, context: ContextAssembly, char: CharacterAgent,
    models_summary: str, direct_vision: bool, retriever: MemoryRetriever | None,
    conversation_data: ConversationData, client: ChatModel,
    get_tools: Callable[..., Awaitable[list[dict]]], timeout: float,
) -> None:
    """以会话的新前缀和工具目录执行一次有界预热调用。"""
    from agent.messages import Everything, EverythingGroup

    adapter, _, base_id = scope_id.partition(":")
    anything: Everything
    if scope_type == "group":
        anything = EverythingGroup(group_id=base_id, adapter_key=adapter)
    else:
        anything = Everything(uid=base_id, adapter_key=adapter)
    entity_scope = anything.entity_scope

    # 永久块（真实流程经召回提升进 context 层；预热不跑检索，直接取 pins 组装）
    permanent_text = ""
    if retriever is not None:
        pinned = await retriever._load_permanent_pins([])
        pin_msgs = await retriever._format_unified_results(pinned) if pinned else []
        permanent_text = str(pin_msgs[0]["content"]) if pin_msgs else ""

    layered = await context.preparation.build_layers(
        [msg["content"] for msg in char.get_personality_msg() if msg.get("content")],
        entity_scope, models_summary, permanent_text, direct_vision=direct_vision,
    )
    persona_text, tools_text, context_text = layered[0], layered[1], layered[2]
    summary_row = await conversation_data.get_conversation_summary(anything)
    conversation = await conversation_data.get_conversation_record_by_everything(anything)
    messages = await context.build_llm_context(
        persona_text=persona_text,
        tools_text=tools_text,
        context_text=context_text,
        memory_msgs=[],
        anything=anything,
        adapter_key=adapter,
        summary_row=summary_row,
        prefetched_conversation=conversation,
        scope=entity_scope,
    )
    llm_client = client if isinstance(client, LLMClient) else None
    if llm_client is None:
        return
    from agent.llm.prompt_cache import decorate_messages, is_anthropic_wire
    messages = decorate_messages(messages, anthropic=is_anthropic_wire(
        llm_client.config.litellm_model or "",
        getattr(llm_client.config, "api_type", "") or "",
    ))
    # 发送边界统一规整：与 ModelInvoker.invoke 一致（decorate 之后 normalize），
    # 剥离 _layer 标签——缺此步会把内部分类标签泄露给供应商（潜在 400）
    messages = normalize_for_send(messages)
    # tools 数组是请求最前段，必须与真实调用一致（缺它前缀从第 0 字节就不匹配）
    tools = await get_tools(adapter, scope=entity_scope)
    log(
        f"缓存预热 [{entity_scope}]: model={llm_client.config.model} "
        f"msgs={len(messages)} tools={len(tools or [])}",
        "DEBUG", tag="缓存",
    )
    # 预热是 1-token 轻调用但总时长必须有界：detached task 无引用无取消，
    # 端点挂起/吊流会泄漏孤儿任务并长期占用缓存亲和小连接池
    async def _consume() -> None:
        async for _delta in llm_client.chat_stream(
            messages, options={"max_tokens": 1}, tools=tools or None,
        ):
            pass

    await asyncio.wait_for(_consume(), timeout=timeout)
