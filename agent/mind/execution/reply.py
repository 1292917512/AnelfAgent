"""对话执行入口：会话隔离、上下文准备、中断与回复收尾。"""


from __future__ import annotations

import time
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.mind.execution.loop import think_loop
from agent.mind.execution.messages import (
    _consume_pending_for_scope,
    snapshot_watermark,
)
from agent.mind.execution.reply_finalize import complete_reply, fail_reply
from agent.mind.execution.state import ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort
from agent.mind.execution.vision import apply_vision
from agent.mind.recall import coordinator
from agent.mind.think_session import think_session
from core.activity import activity_scope
from core.event_bus import (
    EVENT_BEFORE_REPLY,
    event_bus,
)

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
) -> bool:
    """执行回复，异常通过完成事件和过程状态反馈。

    completion 非 None 时由 think_loop 在结束处写入结束原因与完整消息链
    （回复上下文快照），供事件层把 transcript 带出给钩子面消费。
    """
    if completion is None:
        completion = {}
    try:
        await event_bus.emit(EVENT_BEFORE_REPLY, {"phase": "llm_calling"})
        from agent.mind.execution.reply_journal import build_reply_journal
        journal = build_reply_journal(mind, anything.entity_scope)
        if journal is not None:
            previous = await journal.render()
            if previous:
                await mind.conversation_data.append_context(
                    anything, "[前次执行遗留记录] 以下操作不可因前次失败而自动重做，请先核对结果。\n" + previous,
                    role="system",
                )
                await journal.clear()
        await reply_loop(mind, anything, images or [], adapter_key=adapter_key,
                         completion=completion)
    except Exception as exc:
        await fail_reply(mind, anything, exc, completion=completion)
    return completion.get("reason") != "failed"


async def reply_loop(
        mind: "Mind",
        anything: "Everything",
        images: Optional[List["ImageContent"]] = None,
        *,
        adapter_key: str = "",
        completion: Optional[Dict[str, Any]] = None,
) -> None:
    """多轮对话循环入口：处理图片，进入统一思维循环。"""
    mc = mind.config
    scope = anything.entity_scope if anything else ""
    from agent.messages import parse_entity_scope
    adapter_key = parse_entity_scope(scope)[1] or adapter_key
    from agent.channel.reply_policy import get_reply_policy
    from agent.memory.recall_policy import recall_turn
    from core.tool_context import tool_request

    channel_policy = get_reply_policy(adapter_key)
    turn_id = uuid.uuid4().hex[:8]
    interrupts = getattr(mind, "interrupts", None)
    # 绑定本轮信号前清理旧请求，保留回复启动窗口内收到的停止。
    if scope and interrupts is not None:
        interrupts.clear_before(scope, getattr(mind, "_reply_activated_at", {}).get(scope, 0))
    with think_session(mind.tool_assembly, scope), tool_request(
        scope, str(getattr(anything, "uid", "") or ""),
        message_id=str(getattr(anything, "_trace_message_id", "") or getattr(anything, "adapter_message_id", "") or ""),
    ), interrupts.execution(turn_id, scope) if interrupts is not None else nullcontext():
        label = anything.get_text_content() or anything._trace_input_preview
        async with activity_scope(
            turn_id, scope, label=label, kind="conversation", can_stop=True,
            trigger=anything.message_kind,
            initiator={"id": str(anything.uid or ""), "name": str(getattr(anything, "user_name", "") or "")},
        ) as activity, \
                recall_turn(scope, anything.recall_policy, default_policy=channel_policy.recall_policy):
            if completion is not None:
                completion["turn_id"] = activity.turn_id
            _interrupts = getattr(mind, "interrupts", None)
            # 推送水位必须在 base 快照之前读取：快照与水位读取之间的到达窗口内，
            # 推送既未进入 base、又被 drain_inflight 按 seq≤水位丢弃，导致吞推送。
            push_watermark = 0
            push_hub = getattr(mind, "push_hub", None)
            if push_hub is not None and scope:
                try:
                    push_watermark = push_hub.current_seq(scope)
                except Exception:
                    push_watermark = 0
            async def prepare() -> tuple[List[Dict], List[Dict]]:
                pending_images = list(images or []) + mind.pfc.collect_images(scope=scope)
                pending_media = mind.pfc.collect_media(scope=scope)
                if pending_images or pending_media:
                    mind.tool_assembly.activate_media_tools(pending_images, pending_media)
                if pending_media:
                    from agent.mind.execution.media_pipeline import process_segments
                    media_texts = await process_segments(pending_media, scope)
                    if media_texts:
                        await mind.conversation_data.append_context(anything, "\n".join(media_texts))
                tools = await mind.tool_assembly.get_active_tool_schemas(adapter_key, scope=scope)
                messages = await coordinator.prepare_recollection(mind, anything=anything)
                if pending_images:
                    messages = await apply_vision(mind, messages, pending_images, anything)
                return tools, messages

            prepared, aborted = await _await_with_abort(
                prepare(), _interrupts.event(scope) if _interrupts is not None and scope else None,
            )
            if aborted:
                reason = (_interrupts.consume(scope) if _interrupts is not None else None) or "用户请求"
                activity.status = "cancelled"
                if completion is not None:
                    completion["reason"] = "interrupted"
                await mind.conversation_data.append_context(
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
