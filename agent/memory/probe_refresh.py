"""从轮内执行线索触发后台回忆，不等待主回复，不监听定时器。

模型可见：有界当前请求与执行摘录供判断，去重结果经现有 provider 补充。
Token 影响：每轮有限次预判和结果判断；无 Jev 时复用普通模型判断回退。
缓存影响：仅更新 provider 尾部；不重写消息历史、思考配置或稳定前缀。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

from agent.judgment.evidence import conversation_evidence, evidence_json
from agent.judgment.gate import assess_optional_work
from agent.judgment.types import JsonValue
from core.async_helper import spawn
from core.config import get_config_bool, get_config_float, get_config_int
from core.hooks import current_hook_run
from core.log import log

from .memory_types import RetrievalPlan
from .probe_render import RecallDelta
from .recall_policy import current_recall_policy

if TYPE_CHECKING:
    from .probe import DeepProbeHub, _ProbeState


def clue_key(evidence: list[JsonValue]) -> str:
    """忽略调用 ID 和历史窗口移动，只以最新可见执行内容识别重复线索。"""
    return hashlib.sha256(evidence_json(evidence[-1:]).encode()).hexdigest() if evidence else ""


def refresh_probe(hub: DeepProbeHub, state: _ProbeState, conversation: list[dict[str, Any]]) -> None:
    """每个步骤边界检查新事实，已在途时只更新判断证据，稍后再处理新线索。"""
    state.evidence = conversation_evidence(conversation)
    policy = current_recall_policy(state.scope)
    retriever = state.retriever
    if retriever is None or policy.tags or not policy.background or policy.level not in {"fast", "deep"}:
        return
    if not get_config_bool("memory_probe_enabled", True) or not get_config_bool("memory_probe_inject", True):
        return
    if state.task and not state.task.done():
        return
    key = clue_key(state.evidence)
    if not key or key in state.examined or state.refresh_count >= max(0, min(20, get_config_int("memory_probe_refresh_limit", 4))):
        return
    last = state.evidence[-1]
    if not isinstance(last, dict):
        return
    focus = retriever._extract_focus_query(conversation)
    if last.get("role") == "user":
        if not focus or focus in state.queries or retriever._is_trivial_turn(conversation):
            return
        query = focus
    else:
        query = f"当前请求：{focus}\n执行线索：{last.get('content', '')}"[:1000]
    state.examined.add(key)
    state.queries.add(query)
    state.refresh_count += 1
    revision = state.revision

    async def execute() -> None:
        async with hub._observe(state):
            decision = await assess_optional_work(
                "执行中回忆", "当前执行是否出现尚未覆盖的知识、操作方法或历史细节需求，需要补查记忆或技能？"
                "只依据当前请求和执行事实；普通进度、重复输出、已经解决的问题不需要检索。",
                {"scope": state.scope, "conversation": state.evidence, "already_recalled": state.recalled,
                 "supplement": state.rendered}, llm_fallback=policy.level == "deep",
                timeout=max(0.1, get_config_float("memory_probe_timeout_seconds", 60) / 4),
            )
            if not decision.run or not hub._is_current(state, revision):
                return
            plan = RetrievalPlan(queries=[query])
            if policy.level == "deep":
                await hub._stage("根据执行线索规划回忆")
                plan = await retriever.plan_retrieval(query, timeout=5)
            if not hub._is_current(state, revision):
                return
            lookup = plan.queries[0] if plan.queries else query

            async def memory() -> list[RecallDelta]:
                from .recall_fast import search_local
                results = await search_local(retriever, lookup)
                return await hub._memory_deltas(results, retriever._store, "补充记忆")

            async def discover() -> list[RecallDelta]:
                return await state.discover(lookup) if state.discover else []

            async def publish_source(source: str, operation: Awaitable[list[RecallDelta] | None]) -> None:
                try:
                    deltas = await operation
                    if deltas:
                        await hub._admit(state, revision, deltas, review=True, llm_fallback=policy.level == "deep")
                except Exception as exc:
                    log(f"后台回忆 {source} 失败: {type(exc).__name__}: {exc}", "DEBUG", tag="记忆")
                    run = current_hook_run.get()
                    if run:
                        run.status = "error"
                        run.detail = f"{run.detail}\n{source}: {type(exc).__name__}"
                        await run.publish()

            await hub._stage("并行检索记忆与技能")
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(publish_source("记忆", memory()))
                tasks.create_task(publish_source("技能", discover()))
                if policy.level == "deep" and (plan.deep_needed or plan.node_keys):
                    tasks.create_task(publish_source("图谱", hub._probe(state, plan, retriever._store, revision=revision, review=True)))

    state.task = spawn(execute(), name=f"memory.probe.{state.scope}")
