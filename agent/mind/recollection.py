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
    build_memory_status_block,
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

    # 相关实体 scope（画像注入与关系网络注入共用同一份参与人集合）
    scope_source = conversation_list[-30:] if len(conversation_list) > 30 else conversation_list
    related_scopes = mind._extract_related_scopes(scope_source, entity_scope)
    if anything:
        for s in mind._extract_scopes_from_anything(anything, entity_scope):
            if s not in related_scopes:
                related_scopes.insert(0, s)

    async def _recall_memory() -> Tuple[List[Dict], List[Dict]]:
        if not mind.retriever:
            return [], []
        profile_msgs, recall_msgs = await mind.retriever.recall_split(
            tail, entity_scope=entity_scope, related_scopes=related_scopes,
            query_vec=query_vec,
        )
        log(f"语义召回: {len(recall_msgs)} 条, 画像: {len(profile_msgs)} 条", tag="思维")
        return profile_msgs, recall_msgs

    async def _load_relations() -> List[Dict]:
        """关系网络快照：当前会话参与实体之间的已知关系（独立注入块，不吃记忆预算）。"""
        if not mind.retriever:
            return []
        all_scopes = ([entity_scope] if entity_scope else []) + [
            s for s in related_scopes if s != entity_scope
        ]
        return await mind.retriever.load_relation_snippets(all_scopes)

    async def _load_goals() -> List[Dict]:
        """活跃目标快照：让 AI 在普通对话中始终感知自己的进行中目标。"""
        if not mind.memory_store:
            return []
        try:
            from core.config import get_config_bool
            if not get_config_bool("goals_inject_enabled", True):
                return []
            from agent.planning.tools import build_goals_injection
            content = await build_goals_injection(mind.memory_store, scope=entity_scope)
        except Exception as exc:
            log(f"活跃目标注入构建失败: {exc}", "DEBUG", tag="思维")
            return []
        return [{"role": "system", "content": content}] if content else []

    # 五条召回路径互相独立（各自读 DB/检索，无共享状态），并行执行
    (profile_msgs, memory_msgs), relation_msgs, goal_msgs, (cross_recall_msgs, recalled_scopes), skill_msgs = await asyncio.gather(
        _recall_memory(),
        _load_relations(),
        _load_goals(),
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
    # （画像在前优先保留，预算从整体尾部截断）
    combined = mind._apply_memory_budget(profile_msgs + memory_msgs)
    profile_msgs = combined[:len(profile_msgs)]
    memory_msgs = combined[len(profile_msgs):]

    # 对话摘要窗口：固定摘要块（折叠周期内字节稳定，作历史前缀缓存锚点）
    summary_row: Optional[Dict] = None
    if anything:
        try:
            summary_row = await mind.conversation_data.get_conversation_summary(anything)
        except Exception as exc:
            log(f"对话摘要获取失败: {exc}", "DEBUG", tag="思维")

    # Prompt 分层构建（参考 hermes 三层架构）：
    # stable 人设块（人设+环境+静态指南）长期冻结，stable 工具块（目录+规则）随工具集重建，
    # context 层（便签）低频重建，volatile 层（语义召回等）每轮构建并置于其后，保证前缀缓存命中。
    models_summary = mind._get_models_summary()
    persona_text, tools_text, context_text, persona_hit, tools_hit, context_hit, status_text = (
        await mind._build_layered_prompts(anything, models_summary)
    )

    await event_bus.emit(EVENT_THINKING_CONTEXT_BUILD, {
        "memory_msgs_count": len(memory_msgs),
        "stable_cache_hit": persona_hit and tools_hit,
        "context_cache_hit": context_hit,
    })

    return await mind.pfc.build_llm_context(
        persona_text=persona_text,
        tools_text=tools_text,
        context_text=context_text,
        memory_msgs=memory_msgs,
        anything=anything,
        adapter_key=getattr(anything, "adapter_key", ""),
        target_id=mind._resolve_target_id(anything),
        models_summary=models_summary,
        prefetched_conversation=conversation_list,
        scope=entity_scope,
        profile_msgs=profile_msgs,
        relation_msgs=relation_msgs,
        goal_msgs=goal_msgs,
        summary_row=summary_row,
        status_text=status_text,
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
            skill_body = skill.content[:800]
            if len(skill.content) > 800:
                skill_body += "\n（内容较长已截断，get_skill 可读全文）"
            skill_lines.append(
                f"## {skill.name} — {skill.description}\n{skill_body}"
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
) -> Tuple[str, str, str, bool, bool, bool, str]:
    """构建 stable 人设块 / stable 工具块 / context 层三段提示（经 PromptCacheManager 缓存复用）。

    人设块：人设 + 运行环境 + 静态指南——与工具无关，指纹不含工具版本因子，
    工具激活/发现/清理不会使其失效，长期命中（缓存前缀中最大最稳定的段）。
    工具块：工具目录 + 使用规则 + 媒体规则——随工具集变化经 stable_fingerprint 重建。
    context 层：动态便签（当前状态/教导/规则）+ 文件索引，仅编辑时重建。

    文件型层通过 FileLayerCache 做 mtime O(1) 快检，未变时跳过 I/O。

    Returns:
        (persona_text, tools_text, context_text, persona_hit, tools_hit, context_hit, status_text)
    """
    from agent.mind.prompt_layers import (
        LAYER_CONTEXT,
        LAYER_STABLE_PERSONA,
        LAYER_STABLE_TOOLS,
        prompt_cache_manager,
    )

    scope = mind._resolve_entity_scope(anything)

    persona_parts = [
        msg["content"] for msg in mind.char.get_personality_msg() if msg.get("content")
    ]
    direct_vision = mind._direct_vision()

    # --- stable 人设块：人设 + 环境 + 静态指南（指纹不含工具因子） ---
    from agent.mind.context_assembly import _env_info_block
    notes_path = get_notes_path()
    static_guide, _ = mind._file_cache.get_or_load(notes_path, build_static_guide)
    persona_hash = prompt_cache_manager.compute_hash(
        *persona_parts, static_guide, _env_info_block(),
    )
    persona_text, persona_hit = prompt_cache_manager.get_or_build(
        scope, LAYER_STABLE_PERSONA, persona_hash,
        lambda: mind.pfc.context_assembly.build_persona_block(persona_parts, static_guide),
    )

    # --- stable 工具块：工具目录 + 规则（指纹含工具版本门控） ---
    tools_hash = prompt_cache_manager.compute_hash(
        mind.pfc.stable_fingerprint(models_summary, direct_vision),
    )
    tools_text, tools_hit = prompt_cache_manager.get_or_build(
        scope, LAYER_STABLE_TOOLS, tools_hash,
        lambda: mind.pfc.context_assembly.build_tools_block(models_summary, direct_vision),
    )

    # --- context 层：动态便签 + 文件索引 ---
    dynamic_notes, _ = mind._file_cache.get_or_load(notes_path, build_dynamic_notes)
    file_index, _ = mind._file_cache.get_or_load(get_memory_dir(), build_file_index_block)
    # 记忆状态区块（心跳维护，周期性变化）不入 context 层，尾部动态区独立注入
    status_text, _ = mind._file_cache.get_or_load(notes_path, build_memory_status_block)
    context_parts = [p for p in (dynamic_notes, file_index) if p]
    if not context_parts:
        context_parts = [build_notes_empty_hint()]
    context_hash = prompt_cache_manager.compute_hash(*context_parts)
    context_text, context_hit = prompt_cache_manager.get_or_build(
        scope, LAYER_CONTEXT, context_hash,
        lambda: "[个人笔记/便签记忆]\n" + "\n\n".join(context_parts),
    )
    return persona_text, tools_text, context_text, persona_hit, tools_hit, context_hit, status_text


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

    仅在群聊场景下有意义。adapter 继承自当前群 scope（成员与群同频道）。
    """
    if not primary_scope.startswith("group_"):
        return []
    from agent.messages import build_entity_scope, parse_entity_scope
    _, adapter, _, _ = parse_entity_scope(primary_scope)
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
            scope = build_entity_scope("user", adapter, uid)
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes


def _extract_scopes_from_anything(
        mind: "Mind", anything: "Everything", primary_scope: str,
) -> List[str]:
    """从当前消息对象提取发送者 uid 和 [at_uid:xxx] 中的 uid。"""
    from agent.messages import build_entity_scope
    adapter = str(getattr(anything, "adapter_key", "") or "")
    seen: set[str] = {primary_scope}
    scopes: List[str] = []
    if anything.uid and anything.uid not in (0, "0"):
        scope = build_entity_scope("user", adapter, str(anything.uid))
        if scope not in seen:
            seen.add(scope)
            scopes.append(scope)
    content = anything.get_text_content() if hasattr(anything, "get_text_content") else ""
    if content:
        for m in mind._RELATED_UID_RE.finditer(content):
            uid = m.group(1)
            if uid == "all":
                continue
            scope = build_entity_scope("user", adapter, uid)
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes
