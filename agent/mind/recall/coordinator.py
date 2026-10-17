"""首轮召回流程：准备事实，交由上下文层组装，发布观测事件。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Dict, List, Optional

from agent.context.observation import emit_context_summary
from agent.memory import recall_context  # noqa: F401
from agent.mind.recall.inputs import collect_recollection_inputs
from core.activity import EVENT_ACTIVITY_CONTEXT
from core.event_bus import event_bus

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


async def get_recollection(
        mind: "Mind",
        conversation_list: Optional[List[Dict]] = None,
        anything: Optional["Everything"] = None,
        *, lean: bool = False,
) -> List[Dict]:
    """观测上下文准备过程，成功后展示实际装配结果，失败仍交给调用方处理。"""
    started = time.monotonic()
    await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {"status": "running"})
    try:
        messages = await _get_recollection(mind, conversation_list, anything, lean=lean)
    except BaseException as exc:
        await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {
            "status": "interrupted" if isinstance(exc, asyncio.CancelledError) else "error",
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise
    await emit_context_summary(messages, round((time.monotonic() - started) * 1000))
    return messages


async def _get_recollection(
        mind: "Mind",
        conversation_list: Optional[List[Dict]] = None,
        anything: Optional["Everything"] = None,
        *,
        lean: bool = False,
) -> List[Dict]:
    """按当前会话准备召回材料，交由上下文层组装。

    Args:
        conversation_list: 外部传入的对话历史（Introspection 场景）。
            若为 None，内部自动从 DB 获取最新对话。
        anything: 消息对象，用于确定对话 scope。
        lean: 仅保留人设、工具与永久记忆，跳过环境和语义检索；内部任务
            经工具按需取数，共享稳定前缀。计划状态由动态提供者负责。
    """
    # 若未传入对话历史，从 DB 实时获取
    if conversation_list is None:
        conversation_list = await mind.get_conversation(anything) if anything else []

    entity_scope = mind._resolve_entity_scope(anything)
    current_adapter = getattr(anything, "adapter_key", "") or ""
    inputs = await collect_recollection_inputs(
        mind, conversation_list, anything, scope=entity_scope, adapter=current_adapter, lean=lean,
    )
    return await mind.context.preparation.build_recollection(
        inputs, conversation_list, anything,
        persona_parts=[msg["content"] for msg in mind.char.get_personality_msg() if msg.get("content")],
        models_summary=mind._get_models_summary(), direct_vision=mind._direct_vision(), lean=lean,
    )


async def _match_skills(
        mind: "Mind",
        base_query: str,
        *,
        query_vec: Optional[List[float]] = None,
        query_vec_task: asyncio.Future[list[float] | None] | None = None,
        scope: str = "",
        planned_queries: Optional[List[str]] = None,
) -> List[Dict]:
    """消费本会话的技能手势，交给技能系统匹配与生成召回材料。"""
    if not mind._skills_enabled():
        return []
    from agent.skills.recall import match_recalled_skills
    gestures = mind._pending_skill_gestures.pop(scope, []) if scope else []
    return await match_recalled_skills(
        mind.skill_store, mind.skill_matcher, gestures, mind.pfc.add_temporary, base_query,
        query_vec=query_vec, query_vec_task=query_vec_task, scope=scope, planned_queries=planned_queries,
    )
