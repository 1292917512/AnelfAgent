"""并发读取上下文事实，仅在数据依赖处等待。

模型可见：画像、召回、技能与摘要的内容和装配顺序保持不变。
Token 影响：复用检索计划和查询向量，不增加模型请求或注入块。
缓存影响：只改变取数调度；稳定前缀与动态区的边界不变。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.recall_policy import current_recall_policy
from core.hooks import HookMode, hook_execution
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


@dataclass
class RecollectionInputs:
    profiles: list[dict] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    cross_channel: list[dict] = field(default_factory=list)
    recalled_scopes: set[str] = field(default_factory=set)
    skills: list[dict] = field(default_factory=list)
    profile_blocks: list[str] = field(default_factory=list)
    hub: str = ""
    directives: str = ""
    repeat_hint: str = ""
    summary: dict | None = None


async def collect_recollection_inputs(
    mind: Mind, conversation: list[dict], anything: Everything | None,
    *, scope: str, adapter: str, lean: bool,
) -> RecollectionInputs:
    """同时启动独立读取；技能只等待记忆计划，取消时收束全部本轮读取。"""
    result = RecollectionInputs()
    tail = conversation[-10:]
    policy = current_recall_policy(scope)
    trivial = MemoryRetriever._is_trivial_turn(tail)
    query = MemoryRetriever._extract_query(tail) if not lean and not trivial and policy.level == "deep" else ""
    related = mind._extract_related_scopes(conversation[-30:], scope)
    if anything:
        for item in mind._extract_scopes_from_anything(anything, scope):
            if item not in related:
                related.insert(0, item)
    scopes = ([scope] if scope else []) + [item for item in related if item != scope]
    plan: dict[str, Any] = {}
    vector_task: asyncio.Task[list[float] | None] | None = None

    async def embed() -> list[float] | None:
        try:
            async with asyncio.timeout(2):
                return await mind.embedder.embed_query(query)
        except Exception as exc:
            log(f"上下文向量预取失败: {type(exc).__name__}", "DEBUG", tag="记忆")
            return None

    async def recall_and_match() -> None:
        if mind.retriever:
            async with hook_execution("memory.recall", "context_prepare", HookMode.SYNC, scope=scope) as run:
                if policy.level != "deep":
                    from agent.memory.recall_fast import recall_local
                    result.profiles, result.memories = await recall_local(
                        mind.retriever, tail, scope=scope, related_scopes=related, policy=policy)
                else:
                    result.profiles, result.memories = await mind.retriever.recall_split(
                        tail, entity_scope=scope, related_scopes=related,
                        query_vec_task=vector_task, fire_probe=policy.background, plan_out=plan,
                    )
                run.detail = f"{policy.level} · {len(result.profiles)} 个画像块 · {len(result.memories)} 个记忆块"
            from agent.memory.probe import deep_probe_hub
            deep_probe_hub.prepare(scope, tail, mind.retriever)
            log(f"语义召回: {len(result.memories)} 条, 画像: {len(result.profiles)} 条", tag="思维")
        result.skills = await mind._match_skills(
            query, query_vec_task=vector_task, scope=scope, planned_queries=plan.get("queries"))

    async def relations() -> None:
        if mind.retriever and not policy.tags and policy.level not in {"off", "tags"}:
            result.relations = await mind.retriever.load_relation_snippets(scopes)

    async def cross_channel() -> None:
        if not trivial and policy.level == "deep":
            result.cross_channel, result.recalled_scopes = await mind._recall_cross_channel(
                tail, adapter, scope, query_vec_task=vector_task)

    async def pins() -> None:
        if mind.retriever is not None:
            pinned = await mind.retriever._load_permanent_pins([])
            result.memories = await mind.retriever._format_unified_results(pinned) if pinned else []

    async def hub() -> None:
        if mind.memory_store is not None:
            from agent.memory.hub import load_hub_block
            result.hub = await load_hub_block(mind.memory_store)

    async def repeat_hint() -> None:
        try:
            from agent.memory.anti_repeat import build_repeat_hint
            result.repeat_hint = await build_repeat_hint(scope)
        except Exception as exc:
            log(f"复读提示获取失败: {exc}", "DEBUG", tag="思维")

    async def summary() -> None:
        if anything:
            try:
                result.summary = await mind.conversation_data.get_conversation_summary(anything)
            except Exception as exc:
                log(f"对话摘要获取失败: {exc}", "DEBUG", tag="思维")

    async def profile_blocks() -> None:
        store = mind.memory_store
        if store is None or policy.tags or policy.level in {"off", "tags"}:
            return
        from agent.memory.reflection_lifecycle import (
            load_confirmed_block,
            load_verification_block,
            scope_tags_from_entity_scopes,
        )
        from agent.memory.self_profile import load_self_profile, render_self_profile_block

        async def self_profile() -> str:
            return render_self_profile_block(await load_self_profile(store))

        tags = scope_tags_from_entity_scopes(scopes)
        # 独立失败只损失当前块，结果按固定位置装配，不按返回顺序装配。
        blocks = await asyncio.gather(
            self_profile(),
            load_confirmed_block(store, visible_tags=tags),
            load_verification_block(store, visible_tags=tags),
            return_exceptions=True,
        )
        for block in blocks:
            if isinstance(block, BaseException):
                if not isinstance(block, Exception):
                    raise block
                log(f"画像补充获取失败: {block}", "DEBUG", tag="思维")
                result.profile_blocks.append("")
            else:
                result.profile_blocks.append(block)

    if scope:
        try:
            from agent.memory.user_directives import build_directives_block
            result.directives = build_directives_block(scope)
        except Exception as exc:
            log(f"会话纪律获取失败: {exc}", "DEBUG", tag="思维")
    async with asyncio.TaskGroup() as tasks:
        if query:
            vector_task = tasks.create_task(embed())
        tasks.create_task(hub())
        tasks.create_task(summary())
        if lean:
            tasks.create_task(pins())
        else:
            tasks.create_task(recall_and_match())
            tasks.create_task(relations())
            tasks.create_task(cross_channel())
            tasks.create_task(profile_blocks())
            if scope:
                tasks.create_task(repeat_hint())
    return result
