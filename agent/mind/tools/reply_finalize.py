"""思维循环的收尾块：回复入口、结束处理、执行摘要与状态清理。

从 think_loop 拆出；reply_loop 对 think_loop 的委托采用函数级延迟导入，
避免与 think_loop（依赖本模块的 finish_think/complete_reply）形成循环引用。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Dict, List, Optional

from agent.channel.reply_route import deliver_text, target_from_anything
from agent.mind.tools.round_helpers import _consume_pending_for_scope
from agent.mind.tools.vision import apply_vision
from core.event_bus import EVENT_AFTER_REPLY, EVENT_BEFORE_REPLY, event_bus
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ImageContent
    from agent.messages import Everything
    from agent.mind.mind import Mind


# ==================================================================
# 公共入口
# ==================================================================

async def reply_entry(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
        *,
        adapter_key: str = "",
) -> None:
    """执行回复，异常时发送错误提示。"""
    await event_bus.emit(EVENT_BEFORE_REPLY, {"phase": "llm_calling"})
    try:
        await reply_loop(mind, anything, images or [], adapter_key=adapter_key)
    except Exception as exc:
        log(f"reply 异常: {type(exc).__name__}: {exc}", "ERROR", tag="思维")
        error_msg = f"抱歉，处理消息时出错了: {type(exc).__name__}: {exc}"
        await _send_reply_error(anything, error_msg)
        await complete_reply(mind, anything, error_msg, 0, error=True)


async def _send_reply_error(anything: Everything, error_msg: str) -> None:
    """reply 异常时主动把错误提示发送到来源频道（避免用户端无反馈地空等）。"""
    try:
        target = target_from_anything(anything)
        if target is not None:
            await deliver_text(target, error_msg)
    except Exception as exc:
        log(f"错误提示发送失败: {exc}", "DEBUG", tag="思维")


async def reply_loop(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
        *,
        adapter_key: str = "",
) -> None:
    """多轮对话循环入口：处理图片，委托给统一思维循环。"""
    from agent.mind.think_session import think_session

    # 延迟导入：think_loop 依赖本模块的 finish_think/complete_reply，避免循环引用
    from agent.mind.tools.think_loop import ThinkMode, think_loop

    mc = mind._get_mind_config()
    # adapter_key 优先使用调用方传入（按 scope 隔离），回退到共享状态（兼容旧路径）
    if not adapter_key:
        adapter_key = mind._resolve_adapter_key()
    scope = mind._resolve_entity_scope(anything) if anything else ""
    with think_session(mind, scope):
        # 会话开始清理历史中断信号，避免上一轮遗留请求误杀新会话
        _interrupts = getattr(mind, "interrupts", None)
        if scope and _interrupts is not None:
            _interrupts.clear(scope)
        active_tools = await mind.pfc.get_active_tool_schemas(adapter_key, scope=scope)
        base_messages = await mind.get_recollection(anything=anything)
        # 历史快照已覆盖该 scope 当前全部消息：消费到达时入队的待处理条目，
        # 避免快照内消息在周期结束后另起周期导致重复回复
        if anything:
            _consume_pending_for_scope(mind, anything)
        if images:
            base_messages = await apply_vision(mind, base_messages, images, anything)

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
            adapter_key=adapter_key,
        )


async def save_ai_thought(mind: Mind, anything: Optional[Everything], text: str) -> None:
    """将 AI 纯文本输出存入对话历史（assistant 角色 + 思考标签）。

    纯文本不是回复，仅作为推理草稿保留——下一轮 LLM 能看到自己刚才说了啥，
    避免 AI 在提示词驱动下重复同样的纯文本。仅首次纯文本入库；后续重复
    不入库，防止历史中的"长思考"模式被模型模仿强化。
    """
    if not anything or not text:
        return
    tagged = f"[思维] {text}"
    await mind._add_system_context(anything, tagged, role="assistant")


# ==================================================================
# 思维循环结束处理
# ==================================================================

async def finish_think(
        mind: Mind,
        anything: Everything,
        execution_steps: List[str],
        iterations: int,
        tool_chain: Optional[List[Dict]] = None,
) -> None:
    """思维循环结束处理：工具摘要入库 + 经 EVENT_AFTER_REPLY 交给技能评审。

    Plan 收敛不在此处：正常结束路径由 think_loop 的 ``_finish_round`` 在调用
    本函数前统一执行 ``tracker.finalize_plan``；异常路径（中断/安全上限）
    直接调本函数，plan 保持 active 可续——语义分层更准确。
    """
    execution_summary = _build_execution_summary(tool_chain, execution_steps)
    if execution_summary.startswith("[已执行操作摘要]"):
        # 工具执行记录持久化到对话历史（system 角色），
        # 等价于主流 function calling 历史中的 assistant(tool_calls) + tool results。
        # 不再写入短期记忆（DB 历史每轮都会加载，避免双重注入）。
        await mind._add_system_context(
            anything,
            execution_summary,
            role="system",
        )

    await complete_reply(
        mind, anything, "", iterations,
        tool_chain=tool_chain,
        execution_summary=execution_summary,
    )


def _build_execution_summary(
        tool_chain: Optional[List[Dict]],
        execution_steps: List[str],
) -> str:
    """从工具链构建执行摘要；无工具结果时回退到步骤日志。

    摘要同时用于：对话历史入库（仅工具摘要）与 EVENT_AFTER_REPLY.execution_summary
    （SkillReviewer 契约）。
    """
    if tool_chain:
        call_map: Dict[str, str] = {}  # tool_call_id → "name(args_preview)"
        for msg in tool_chain:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args_raw = fn.get("arguments", "") or ""
                    try:
                        args_obj = json.loads(args_raw)
                        # send_message 的 content 是 AI 回复本体（已记录为 assistant），
                        # 摘要中剔除避免与 assistant 记录重复
                        if name == "send_message":
                            args_obj = {k: v for k, v in args_obj.items() if k != "content"}
                        args_preview = ", ".join(
                            f"{k}={v}" for k, v in args_obj.items()
                        )
                    except Exception:
                        args_preview = args_raw
                    call_map[tc_id] = f"{name}({args_preview})"

        result_lines: List[str] = []
        tool_idx = 0
        for msg in tool_chain:
            if msg.get("role") == "tool":
                tool_idx += 1
                tc_id = msg.get("tool_call_id", "")
                call_sig = call_map.get(tc_id, f"tool#{tool_idx}")
                result = _summarize_tool_result_for_log(
                    call_map.get(tc_id, ""), msg.get("content") or "",
                )
                result_lines.append(f"  #{tool_idx} {call_sig} → {result}")

        if result_lines:
            return (
                f"[已执行操作摘要] 本轮共执行 {len(result_lines)} 次工具\n"
                + "\n".join(result_lines)
            )

    if execution_steps:
        return "[执行步骤]\n" + "\n".join(execution_steps[-20:])
    return ""


def _summarize_tool_result_for_log(call_sig: str, result: str) -> str:
    """生成操作摘要中的工具结果预览。

    send_message 的结果 JSON 含完整回复 content（已记录为 assistant），
    此处只保留发送状态，避免与 assistant 记录重复。
    """
    if call_sig.startswith("send_message"):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                ok = parsed.get("success") is not False
                target = parsed.get("target_id", "")
                return f"{'已发送' if ok else '发送失败'}" + (f" -> {target}" if target else "")
        except (json.JSONDecodeError, TypeError):
            pass
        return "已发送"
    return result[:200]


# ==================================================================
# 回复完成与状态清理
# ==================================================================

async def complete_reply(
        mind: Mind,
        anything: Everything,
        content: str,
        iterations: int,
        *,
        error: bool = False,
        tool_chain: Optional[List[Dict]] = None,
        execution_summary: str = "",
) -> None:
    """清理回复状态并发出完成事件。

    AI 的最终输出已由投递路径（send_message / 纯文本自动投递 / end_reply 附带正文）以
    assistant 角色写入对话历史，此处不再重复记录。

    EVENT_AFTER_REPLY.execution_summary 是 SkillReviewer 的唯一评审材料来源。
    """
    from agent.mind.autonomous import MindPhase

    mind._set_phase(MindPhase.REPLYING)
    content = (content or "").strip()

    await event_bus.emit(EVENT_AFTER_REPLY, {
        "scope": getattr(anything, "entity_scope", "") if anything is not None else "",
        "content": content[:100] if content else "",
        "iterations": iterations,
        "error": error,
        "execution_summary": execution_summary,
    })
