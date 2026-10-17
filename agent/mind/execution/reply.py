"""对话执行入口：会话隔离、上下文准备、中断与回复收尾。"""


from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.mind.execution.loop import think_loop
from agent.mind.execution.messages import (
    _consume_pending_for_scope,
    snapshot_watermark,
)
from agent.mind.execution.reply_finalize import complete_reply
from agent.mind.execution.state import ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort
from agent.mind.execution.vision import apply_vision
from agent.mind.think_session import think_session
from core.activity import activity_scope
from core.event_bus import (
    EVENT_BEFORE_REPLY,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ImageContent
    from agent.messages import Everything
    from agent.mind.mind import Mind


async def reply_entry(
        mind: "Mind",
        anything: "Everything",
        images: Optional[List["ImageContent"]] = None,
        *,
        adapter_key: str = "",
        completion: Optional[Dict[str, Any]] = None,
) -> None:
    """执行回复，异常通过完成事件和过程状态反馈。

    completion 非 None 时由 think_loop 在结束处写入结束原因与完整消息链
    （回复上下文快照），供事件层把 transcript 带出给钩子面消费。
    """
    await event_bus.emit(EVENT_BEFORE_REPLY, {"phase": "llm_calling"})
    try:
        await reply_loop(mind, anything, images or [], adapter_key=adapter_key,
                         completion=completion)
    except Exception as exc:
        log(f"reply 异常: {type(exc).__name__}: {exc}", "ERROR", tag="思维")
        error_msg = f"抱歉，处理消息时出错了: {type(exc).__name__}: {exc}"
        await complete_reply(mind, anything, error_msg, 0, error=True,
                             completion=completion)


async def reply_loop(
        mind: "Mind",
        anything: "Everything",
        images: Optional[List["ImageContent"]] = None,
        *,
        adapter_key: str = "",
        completion: Optional[Dict[str, Any]] = None,
) -> None:
    """多轮对话循环入口：处理图片，进入统一思维循环。"""
    mc = mind._get_mind_config()
    scope = mind._resolve_entity_scope(anything) if anything else ""
    from agent.messages import parse_entity_scope
    adapter_key = parse_entity_scope(scope)[1] or adapter_key
    from agent.channel.reply_policy import get_reply_policy
    from agent.memory.recall_policy import recall_turn
    from core.tool_context import tool_request

    channel_policy = get_reply_policy(adapter_key)
    with think_session(mind, scope), tool_request(
        scope, str(getattr(anything, "uid", "") or ""),
        message_id=str(getattr(anything, "_trace_message_id", "") or getattr(anything, "adapter_message_id", "") or ""),
    ):
        label = anything.get_text_content() or anything._trace_input_preview
        async with activity_scope(
            uuid.uuid4().hex[:8], scope, label=label, kind="conversation",
            trigger=anything.message_kind,
            initiator={"id": str(anything.uid or ""), "name": str(getattr(anything, "user_name", "") or "")},
        ) as activity, \
                recall_turn(scope, anything.recall_policy, default_policy=channel_policy.recall_policy):
            # 会话开始清理历史中断信号，避免上一轮遗留请求误杀新会话；
            # 只清激活时刻之前的——启动窗口内用户发的"停止"是合法中断
            _interrupts = getattr(mind, "interrupts", None)
            if scope and _interrupts is not None:
                _interrupts.clear_before(
                    scope, getattr(mind, "_reply_activated_at", {}).get(scope, 0),
                )
            # 推送水位必须在 base 快照之前读取：快照与水位读取之间的到达窗口内，
            # 推送既不随短期记忆进 base、又被 drain_inflight 按 seq≤水位 丢弃，导致吞推送
            push_watermark = 0
            push_hub = getattr(mind, "push_hub", None)
            if push_hub is not None and scope:
                try:
                    push_watermark = push_hub.current_seq(scope)
                except Exception:
                    push_watermark = 0
            async def prepare() -> tuple[List[Dict], List[Dict]]:
                tools = await mind.pfc.get_active_tool_schemas(adapter_key, scope=scope)
                messages = await mind.get_recollection(anything=anything)
                if images:
                    messages = await apply_vision(mind, messages, images, anything)
                return tools, messages

            prepared, aborted = await _await_with_abort(
                prepare(), _interrupts.event(scope) if _interrupts is not None and scope else None,
            )
            if aborted:
                reason = (_interrupts.consume(scope) if _interrupts is not None else None) or "用户请求"
                activity.status = "cancelled"
                if completion is not None:
                    completion["reason"] = "interrupted"
                await mind._add_system_context(
                    anything, f"[系统] 本次回复在准备上下文时被中断（{reason}），尚未开始模型和工具执行。",
                    role="system",
                )
                await complete_reply(mind, anything, "", 0, completion=completion, turn_id=activity.turn_id)
                return
            active_tools, base_messages = prepared
            # 历史快照已覆盖该 scope 当前全部消息：消费到达时入队的待处理条目，
            # 避免快照内消息在周期结束后另起周期导致重复回复
            if anything:
                _consume_pending_for_scope(mind, anything, through_ts=snapshot_watermark(base_messages)[0])
            await think_loop(
                mind,
                mode=ThinkMode.REPLY,
                tool_chain=[],
                execution_steps=[],
                start_time=time.time(),
                safety_limit=mc.max_tool_iterations,
                collected_text=[],
                active_tools=active_tools,
                anything=anything,
                base_messages=base_messages,
                options={"push_watermark": push_watermark} if push_watermark else None,
                adapter_key=adapter_key,
                completion=completion,
                activity=activity,
            )
