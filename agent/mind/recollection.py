"""Mind 上下文召回块：完整 LLM 上下文构建（回忆 + 提示词分层 + 预算截断）。

函数以 mind 实例为第一参数（与 agent.mind.tools.* 同风格），
Mind 类持有一行薄委托，调用方签名零变化。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.notes import (
    build_dynamic_notes,
    build_file_index_block,
    build_notes_empty_hint,
    build_static_guide,
    get_memory_dir,
    get_notes_path,
)
from core.event_bus import EVENT_THINKING_CONTEXT_BUILD, event_bus
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


async def get_recollection(
        mind: "Mind",
        conversation_list: Optional[List[Dict]] = None,
        anything: Optional["Everything"] = None,
) -> List[Dict]:
    """构建完整 LLM 上下文（人设 + 工作记忆 + 语义召回 + 对话历史）。

    Args:
        conversation_list: 外部传入的对话历史（Introspection 场景）。
            若为 None，内部自动从 DB 获取最新对话。
        anything: 消息对象，用于确定对话 scope。
    """
    # 若未传入对话历史，从 DB 实时获取
    if conversation_list is None:
        conversation_list = await mind.get_conversation(anything) if anything else []

    # 语义记忆召回（用最新对话尾部作为查询上下文）
    entity_scope = mind._resolve_entity_scope(anything)
    tail = conversation_list[-10:] if len(conversation_list) > 10 else conversation_list
    current_adapter = getattr(anything, "adapter_key", "") or ""

    # 查询提取与 embedding 每轮只做一次，三条召回路径（语义/跨频道/技能）共享
    # （embed_query 内部自带超时与降级，永不阻塞对话路径）
    query = MemoryRetriever._extract_query(tail) if tail else ""
    query_vec: Optional[List[float]] = None
    if query:
        query_vec = await mind.embedder.embed_query(query)

    async def _recall_memory() -> List[Dict]:
        if not mind.retriever:
            return []
        scope_source = conversation_list[-30:] if len(conversation_list) > 30 else conversation_list
        related_scopes = mind._extract_related_scopes(scope_source, entity_scope)
        if anything:
            for s in mind._extract_scopes_from_anything(anything, entity_scope):
                if s not in related_scopes:
                    related_scopes.insert(0, s)
        msgs = await mind.retriever.recall(
            tail, entity_scope=entity_scope, related_scopes=related_scopes,
            query_vec=query_vec,
        )
        log(f"语义召回: {len(msgs)} 条", tag="思维")
        return msgs

    # 三条召回路径互相独立（各自读 DB/检索，无共享状态），并行执行
    memory_msgs, (cross_recall_msgs, recalled_scopes), skill_msgs = await asyncio.gather(
        _recall_memory(),
        mind._recall_cross_channel(tail, current_adapter, entity_scope, query_vec=query_vec),
        mind._match_skills(tail, query_vec=query_vec),
    )

    # 跨频道语义召回 + 叙事面包屑
    memory_msgs.extend(cross_recall_msgs)
    narrative = mind._build_cross_channel_narrative(
        current_adapter, entity_scope, recalled_scopes,
    )
    if narrative:
        memory_msgs.append({"role": "system", "content": narrative})

    # 技能匹配注入（volatile 层）：当前对话语义匹配到的经验技能
    memory_msgs.extend(skill_msgs)

    # memory 层预算截断：防止低相关召回/画像/技能过度占用上下文
    memory_msgs = mind._apply_memory_budget(memory_msgs)

    # Prompt 分层构建（参考 hermes 三层架构）：
    # stable 层（人设 + 工具提示）对话内冻结复用，context 层（便签）低频重建，
    # volatile 层（语义召回等）每轮构建并置于其后，保证前缀缓存命中。
    models_summary = mind._get_models_summary()
    stable_text, context_text, stable_hit, context_hit = await mind._build_layered_prompts(
        anything, models_summary,
    )

    await event_bus.emit(EVENT_THINKING_CONTEXT_BUILD, {
        "memory_msgs_count": len(memory_msgs),
        "stable_cache_hit": stable_hit,
        "context_cache_hit": context_hit,
    })

    return await mind.pfc.build_llm_context(
        stable_text=stable_text,
        context_text=context_text,
        memory_msgs=memory_msgs,
        anything=anything,
        adapter_key=getattr(anything, "adapter_key", ""),
        target_id=mind._resolve_target_id(anything),
        models_summary=models_summary,
        anthropic_breakpoint=mind._is_anthropic_model(),
        prefetched_conversation=conversation_list,
        scope=entity_scope,
    )


async def _match_skills(
        mind: "Mind",
        tail: List[Dict],
        *,
        query_vec: Optional[List[float]] = None,
) -> List[Dict]:
    """匹配当前对话相关的技能（并记录使用次数），返回注入消息列表。"""
    if not mind._skills_enabled() or not tail:
        return []
    try:
        query_texts = [
            m.get("content", "") for m in tail
            if isinstance(m.get("content"), str)
        ]
        from core.config import get_config_int
        top_k = get_config_int("skills_match_top_k", 3)
        matched_skills = await mind.skill_matcher.match(
            query_texts, top_k=top_k, query_vec=query_vec,
        )
        if not matched_skills:
            return []
        skill_lines = ["[相关技能] 以下经验可能适用于当前任务，可参考复用："]
        for skill, _score in matched_skills:
            skill_lines.append(
                f"## {skill.name} — {skill.description}\n{skill.content[:800]}"
            )
            mind.skill_store.record_use(skill.name)
        log(f"技能注入: {', '.join(s.name for s, _ in matched_skills)}", "DEBUG", tag="技能")
        return [{
            "role": "system",
            "content": "\n\n".join(skill_lines),
        }]
    except Exception as exc:
        log(f"技能匹配失败: {exc}", "DEBUG", tag="技能")
        return []


async def _build_layered_prompts(
        mind: "Mind",
        anything: Optional["Everything"],
        models_summary: str,
) -> Tuple[str, str, bool, bool]:
    """构建 stable/context 两层提示（经 PromptCacheManager 缓存复用）。

    stable 层：人设 + 工具提示 + 静态指南（记忆系统文档 + 工作流），对话内冻结。
    context 层：动态便签（当前状态/教导/规则）+ 文件索引，仅编辑时重建。

    文件型层通过 FileLayerCache 做 mtime O(1) 快检，未变时跳过 I/O。

    Returns:
        (stable_text, context_text, stable_hit, context_hit)
    """
    from agent.mind.prompt_layers import (
        LAYER_CONTEXT,
        LAYER_STABLE,
        prompt_cache_manager,
    )

    scope = mind._resolve_entity_scope(anything)

    persona_parts = [
        msg["content"] for msg in mind.char.get_personality_msg() if msg.get("content")
    ]
    direct_vision = mind._direct_vision()

    # --- stable 层：人设 + 工具 + 静态指南 ---
    notes_path = get_notes_path()
    static_guide, _ = mind._file_cache.get_or_load(notes_path, build_static_guide)
    stable_hash = prompt_cache_manager.compute_hash(
        *persona_parts,
        mind.pfc.stable_fingerprint(models_summary, direct_vision),
        static_guide,
    )
    stable_text, stable_hit = prompt_cache_manager.get_or_build(
        scope, LAYER_STABLE, stable_hash,
        lambda: mind.pfc.build_stable_layer(
            persona_parts, models_summary, direct_vision, static_guide,
        ),
    )

    # --- context 层：动态便签 + 文件索引 ---
    dynamic_notes, _ = mind._file_cache.get_or_load(notes_path, build_dynamic_notes)
    file_index, _ = mind._file_cache.get_or_load(get_memory_dir(), build_file_index_block)
    context_parts = [p for p in (dynamic_notes, file_index) if p]
    if not context_parts:
        context_parts = [build_notes_empty_hint()]
    context_hash = prompt_cache_manager.compute_hash(*context_parts)
    context_text, context_hit = prompt_cache_manager.get_or_build(
        scope, LAYER_CONTEXT, context_hash,
        lambda: "[个人笔记/便签记忆]\n" + "\n\n".join(context_parts),
    )
    return stable_text, context_text, stable_hit, context_hit


def _apply_memory_budget(msgs: List[Dict]) -> List[Dict]:
    """按预算截断 memory 层，贪心保留先到的消息（高分召回优先）。

    召回路径按 语义 → 跨频道 → 技能 顺序合并，先到的消息相关性更高。
    超预算时截断当前消息并停止，保证总字符不超上限。
    """
    from core.config import get_config_int
    budget = get_config_int("memory_inject_max_chars", 6000)
    total = sum(len(m.get("content", "")) for m in msgs)
    if total <= budget:
        return msgs
    kept: List[Dict] = []
    used = 0
    for m in msgs:
        chars = len(m.get("content", ""))
        if used + chars <= budget:
            kept.append(m)
            used += chars
        else:
            remaining = budget - used
            if remaining > 200:
                kept.append({**m, "content": m["content"][:remaining] + "\n（已截断）"})
            break
    return kept


def _extract_related_scopes(
        mind: "Mind", conversation_tail: List[Dict], primary_scope: str,
) -> List[str]:
    """从对话中提取涉及的用户 uid（发送者 [uid:] + @ 对象 [at_uid:]），构建画像加载列表。

    仅在群聊场景下有意义。
    """
    if not primary_scope.startswith("group_"):
        return []
    seen: set[str] = {primary_scope}
    scopes: List[str] = []
    for msg in conversation_tail:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for m in mind._RELATED_UID_RE.finditer(content):
            uid = m.group(1)
            if uid == "all":
                continue
            scope = f"user_{uid}"
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes


def _extract_scopes_from_anything(
        mind: "Mind", anything: "Everything", primary_scope: str,
) -> List[str]:
    """从当前消息对象提取发送者 uid 和 [at_uid:xxx] 中的 uid。"""
    seen: set[str] = {primary_scope}
    scopes: List[str] = []
    if anything.uid and anything.uid not in (0, "0"):
        scope = f"user_{anything.uid}"
        if scope not in seen:
            seen.add(scope)
            scopes.append(scope)
    content = anything.get_text_content() if hasattr(anything, "get_text_content") else ""
    if content:
        for m in mind._RELATED_UID_RE.finditer(content):
            uid = m.group(1)
            if uid == "all":
                continue
            scope = f"user_{uid}"
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes
