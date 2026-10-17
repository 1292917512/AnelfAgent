"""初始化单次执行的消息水位、工具管线与流式观测。"""


from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Set

from agent.mind.execution.messages import _user_initiated, snapshot_watermark
from agent.mind.execution.result_pipeline import ToolResultPipeline
from agent.mind.execution.state import ExecutionContext, RoundState, ThinkMode
from core.event_bus import event_bus
from core.stream_events import EVENT_ASSISTANT_DELTA

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


def _make_delta_emitter(
        current_scope: str,
        turn_id: str,
) -> Callable[[str, bool], Awaitable[None]]:
    """构造流式过程事件发射器（增量事件供通道订阅，webui 流式渲染）。

    返回的函数附带 ``reset`` 协程属性：流式中途失败回退非流式重试前调用，
    通知通道清空已渲染的增量文本，避免重试全量文本到达后重复显示。
    """
    accumulated = {"text": "", "reasoning": ""}

    async def delta_emitter(delta: str, reasoning: bool) -> None:
        key = "reasoning" if reasoning else "text"
        accumulated[key] += delta
        try:
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": current_scope,
                "turn_id": turn_id,
                "delta": delta,
                "accumulated": accumulated[key],
                "reasoning": reasoning,
            })
        except Exception:
            pass  # 过程事件失败不影响主流程

    async def reset_stream() -> None:
        accumulated["text"] = accumulated["reasoning"] = ""
        try:
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": current_scope,
                "turn_id": turn_id,
                "reset": True,
            })
        except Exception:
            pass  # 过程事件失败不影响主流程

    delta_emitter.reset = reset_stream  # type: ignore[attr-defined]
    return delta_emitter


def _tool_schema_name(schema: Dict) -> str:
    """从 OpenAI function schema 取工具名。"""
    return str(schema.get("function", {}).get("name", ""))


async def _prepare_think_context(
        mind: "Mind",
        mode: ThinkMode,
        tool_chain: List[Dict],
        execution_steps: List[str],
        collected_text: List[str],
        active_tools: List[Dict],
        anything: Optional["Everything"],
        base_messages: Optional[List[Dict]],
        options: Optional[Dict],
        adapter_key: str,
        blocked_tools: Optional[Set[str]] = None,
        completion: Optional[Dict] = None,
        reflect_tool_selectors: Optional[List[str]] = None,
        require_output: bool = False,
        turn_id: str = "",
) -> tuple[ExecutionContext, RoundState]:
    """think_loop 会话初始化：adapter/基线消息/快照水位/守卫/管线/流式探测。"""
    from agent.mind.guardrails import GuardrailController
    from agent.mind.tool_activation import ToolActivationManager

    mode_label = "反思" if mode == ThinkMode.REFLECT else "对话"
    from agent.messages import parse_entity_scope
    current_scope = ToolActivationManager.current_scope()
    adapter_key = (parse_entity_scope(current_scope)[1] or adapter_key) if mode == ThinkMode.REPLY else ""
    if base_messages is None:
        if mode == ThinkMode.REPLY and anything:
            base_messages = await mind.get_recollection(anything=anything)
        else:
            base_messages = []

    mc = mind.config
    # 纯工具模式（可选，默认关）：开启后 LLM 调用强制工具选择（tool_choice=required）
    pure_tool_mode = bool(getattr(mc, "force_tool_use", False))
    # 后台任务等待：等待意图挂起的单次上限与本轮回复累计预算（秒）
    wait_per_round = float(getattr(mc, "background_wait_timeout", 30.0))
    wait_budget = float(getattr(mc, "background_wait_budget", 120.0))
    background = getattr(mind, "background_tasks", None)
    # 工具调用守卫：跟踪本次会话的调用历史，检测死循环
    guardrail = GuardrailController()
    # 工具结果加工管线：脱敏 → 扫描 → 守卫 → 截断（整轮预算在会话内累计）
    pipeline = ToolResultPipeline(mind, guardrail)
    # 实体推送并入水位：base 快照之前到达的推送已随短期记忆进 base，
    # 仅快照后到达的推送轮内注入（防重复）。优先使用调用方在快照前读取的水位。
    push_watermark = 0
    if mode == ThinkMode.REPLY:
        push_watermark = int((options or {}).get("push_watermark", 0) or 0)
        if push_watermark <= 0:
            push_hub = getattr(mind, "push_hub", None)
            if push_hub is not None:
                push_watermark = push_hub.current_seq(current_scope)
    last_merged_ts, last_merged_id = snapshot_watermark(base_messages)

    # 中断注册表（协作式刹车信号；替身 Mind 可能不具备，容忍缺省）
    interrupts = getattr(mind, "interrupts", None)

    # 在途等待点的中断唤醒事件：与请求登记同生命周期的注册表实例
    abort_event = (
        interrupts.event(current_scope)
        if interrupts is not None and current_scope else None
    )

    # 主回复工具日志：仅 REPLY（与 reply_checkpoints 的 phase=reply 同域）；
    # 子代理续跑有 delegation transcript，反思由调用方持有执行记录
    journal = None
    if mode == ThinkMode.REPLY and current_scope:
        from agent.mind.execution.reply_journal import build_reply_journal
        journal = build_reply_journal(mind, current_scope)

    # 流式过程事件：turn_id 标识本轮思维会话
    turn_id = turn_id or uuid.uuid4().hex[:8]

    ctx = ExecutionContext(
        mind=mind,
        require_output=require_output,
        mode=mode,
        anything=anything,
        adapter_key=adapter_key,
        options=options,
        base_messages=base_messages,
        tool_chain=tool_chain,
        execution_steps=execution_steps,
        collected_text=collected_text,
        active_tools=active_tools,
        completion=completion,
        current_scope=current_scope,
        interrupts=interrupts,
        background=background,
        wait_per_round=wait_per_round,
        pure_tool_mode=pure_tool_mode,
        mode_label=mode_label,
        guardrail=guardrail,
        pipeline=pipeline,
        turn_id=turn_id,
        delta_emitter=_make_delta_emitter(current_scope, turn_id),
        abort_event=abort_event,
        journal=journal,
        blocked_tools=frozenset(blocked_tools or ()),
        reflect_tool_selectors=tuple(reflect_tool_selectors or ()),
    )
    state = RoundState(
        auto_reply_eligible=mode == ThinkMode.REPLY and _user_initiated(base_messages),
        wait_budget=wait_budget,
        last_merged_ts=last_merged_ts,
        last_merged_id=last_merged_id,
        push_watermark=push_watermark,
    )
    return ctx, state
