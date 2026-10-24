"""收集召回材料，按依赖并发读取，不负责最终上下文装配。

模型可见：技能候选独立于记忆档位，查询取自对话正文；材料由上下文层按来源装配。
Token 影响：技能复用记忆计划与查询向量；可选 Jev 分级一次批量判断，减少不需要的规划与检索。
缓存影响：本模块只产出材料，不写入稳定前缀；分层与预算裁剪由上下文层负责。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional

from agent.context.inputs import RecollectionInputs
from agent.judgment.evidence import dialogue_messages
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.recall_policy import current_recall_policy
from agent.mind.cross_channel import recall_cross_channel
from agent.mind.recall.scopes import extract_related_scopes, extract_scopes_from_anything
from core.config import get_config_bool, get_config_float
from core.hooks import HookMode, hook_execution
from core.log import log

if TYPE_CHECKING:
    from agent.config import MindConfig
    from agent.memory.embedding import Embedder
    from agent.memory.memory_store import MemoryStore
    from agent.messages import Everything
    from agent.mind.prefrontal_cortex import PrefrontalCortex
    from agent.skills.runtime import SkillRuntime
    from agent.storage.data_center import ConversationData


@dataclass(slots=True)
class Recollection:
    """并发收集召回材料；与对话增量召回共享检索器和技能索引。"""

    config: MindConfig
    pfc: PrefrontalCortex
    conversation_data: ConversationData
    memory_store: MemoryStore | None
    embedder: Embedder
    retriever: MemoryRetriever | None
    skills: SkillRuntime

    async def collect(
        self, conversation: list[dict], anything: Everything | None,
        *, scope: str, adapter: str, lean: bool,
    ) -> RecollectionInputs:
        """并发收集当前会话的记忆、技能、关联与摘要。

        深度档的技能匹配复用记忆计划，其余档位直接匹配对话；查询向量共享。
        精简模式仅读取摘要与永久记忆。
        本轮任务全部收束后返回材料；未处理的异常或取消会取消同组读取。
        """
        result = RecollectionInputs()
        conversation = dialogue_messages(conversation)
        tail = conversation[-10:]
        policy = current_recall_policy(scope)
        deadline = asyncio.get_running_loop().time() + max(1.0, get_config_float("memory_recall_timeout_seconds", 5.0))
        query = MemoryRetriever._extract_query(tail) if not lean else ""
        related = extract_related_scopes(conversation[-30:], scope)
        if anything:
            for item in extract_scopes_from_anything(anything, scope):
                if item not in related:
                    related.insert(0, item)
        scopes = ([scope] if scope else []) + [item for item in related if item != scope]
        plan_ready: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
        vector_task: asyncio.Task[list[float] | None] | None = None

        async def embed() -> list[float] | None:
            try:
                if asyncio.get_running_loop().time() >= deadline:
                    return None
                async with asyncio.timeout_at(min(deadline, asyncio.get_running_loop().time() + 2)):
                    return await self.embedder.embed_query(query)
            except Exception as exc:
                log(f"上下文向量预取失败: {type(exc).__name__}", "DEBUG", tag="记忆")
                return None

        async def recall() -> None:
            if self.retriever:
                async with hook_execution("memory.recall", "context_prepare", HookMode.SYNC, scope=scope) as run:
                    if policy.level != "deep":
                        from agent.memory.recall_fast import recall_local
                        result.profiles, result.memories = await recall_local(
                            self.retriever, tail, scope=scope, related_scopes=related, policy=policy,
                            deadline=deadline, query_vec_task=vector_task)
                    else:
                        result.profiles, result.memories = await self.retriever.recall_split(
                            tail, entity_scope=scope, related_scopes=related,
                            query_vec_task=vector_task, fire_probe=policy.background, plan_ready=plan_ready,
                            deadline=deadline,
                        )
                    run.detail = f"{policy.level} · {len(result.profiles)} 个画像块 · {len(result.memories)} 个记忆块"
                log(f"语义召回: {len(result.memories)} 条, 画像: {len(result.profiles)} 条", tag="思维")
            if not plan_ready.done():
                plan_ready.set_result([])

        async def skills() -> None:
            planned_queries = await asyncio.shield(plan_ready) if policy.level == "deep" else []
            result.skills = await match_skills(
                self.skills, query, query_vec_task=vector_task, scope=scope, planned_queries=planned_queries,
                deadline=deadline)

        async def relations() -> None:
            if self.retriever and not policy.tags and policy.level not in {"off", "tags"}:
                result.relations = await self.retriever.load_relation_snippets(scopes)

        async def cross_channel() -> None:
            if policy.level == "deep":
                from agent.messages import build_entity_scope
                user_scope = (build_entity_scope("user", adapter, str(anything.uid))
                              if anything and adapter and anything.uid not in (None, 0, "0", "") else "")
                if not user_scope and scope.startswith("group_"):
                    from core.tags import etag_all
                    for message in reversed(tail):
                        if message.get("role") != "user" or not isinstance(message.get("content"), str):
                            continue
                        tags = dict(reversed(etag_all(message["content"], prefix_only=True)))
                        if tags.get("channel") == adapter and tags.get("uid") not in (None, "0", ""):
                            user_scope = build_entity_scope("user", adapter, tags["uid"])
                            break
                try:
                    async with asyncio.timeout_at(deadline):
                        result.cross_channel = await recall_cross_channel(
                            conversation, scope, config=self.config, pfc=self.pfc,
                            conversation_data=self.conversation_data, embedder=self.embedder,
                            user_scope=user_scope, query_vec_task=vector_task)
                except TimeoutError:
                    log("跨会话召回准备预算已用尽，保留本地材料", "DEBUG", tag="记忆")

        async def pins() -> None:
            if self.retriever is not None:
                pinned = await self.retriever._load_permanent_pins([])
                result.memories = await self.retriever._format_unified_results(pinned, scope=scope) if pinned else []

        async def repeat_hint() -> None:
            try:
                from agent.memory.anti_repeat import build_repeat_hint
                result.repeat_hint = await build_repeat_hint(scope)
            except Exception as exc:
                log(f"复读提示获取失败: {exc}", "DEBUG", tag="思维")

        async def summary() -> None:
            if anything:
                try:
                    result.summary = await self.conversation_data.get_conversation_summary(anything)
                except Exception as exc:
                    log(f"对话摘要获取失败: {exc}", "DEBUG", tag="思维")

        async def profile_blocks() -> None:
            store = self.memory_store
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

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(summary())
            if query and (policy.level == "deep" or get_config_bool("skills_enabled", True)):
                vector_task = tasks.create_task(embed())
            if not lean:
                from agent.memory.recall_routing import route_recall
                policy, result.recall_notice = await route_recall(tail, scope)
            if lean:
                tasks.create_task(pins())
            else:
                tasks.create_task(recall())
                tasks.create_task(skills())
                tasks.create_task(relations())
                tasks.create_task(cross_channel())
                tasks.create_task(profile_blocks())
                if scope:
                    tasks.create_task(repeat_hint())
        return result


async def match_skills(
        skills: SkillRuntime,
        base_query: str,
        *,
        query_vec: Optional[List[float]] = None,
        query_vec_task: asyncio.Future[list[float] | None] | None = None,
        scope: str = "",
        planned_queries: Optional[List[str]] = None,
        deadline: float | None = None,
) -> List[Dict]:
    """按对话和可用检索计划匹配技能候选，正文由 AI 按需读取。"""
    if not get_config_bool("skills_enabled", True):
        return []
    from agent.skills.recall import match_recalled_skills
    return await match_recalled_skills(
        skills.store, skills.matcher, base_query,
        query_vec=query_vec, query_vec_task=query_vec_task, scope=scope, planned_queries=planned_queries,
        deadline=deadline,
    )
