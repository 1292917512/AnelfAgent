"""首轮召回流程：准备事实，交由上下文层组装。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from agent.context.observation import observe_preparation
from agent.context.preparation import model_context
from agent.memory import recall_context  # noqa: F401
from agent.mind.recall.inputs import collect_recollection_inputs

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


@observe_preparation
async def prepare_recollection(
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
        conversation_list = await mind.conversation_data.get_conversation_record_by_everything(anything) if anything else []

    entity_scope = anything.entity_scope if anything else ""
    current_adapter = getattr(anything, "adapter_key", "") or ""
    inputs = await collect_recollection_inputs(
        mind, conversation_list, anything, scope=entity_scope, adapter=current_adapter, lean=lean,
    )
    models_summary, direct_vision = model_context(mind.models.llm, mind.models.llm_manager)
    return await mind.context.preparation.build_recollection(
        inputs, conversation_list, anything,
        persona_parts=[msg["content"] for msg in mind.char.get_personality_msg() if msg.get("content")],
        models_summary=models_summary, direct_vision=direct_vision, lean=lean,
    )
