"""思维循环的收尾块：结束处理、执行摘要与状态清理。

回复入口（reply_entry/reply_loop）在 think_loop 模块（它同时依赖本模块的
finish_think/complete_reply 收尾，入口与循环同层避免双向依赖）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Dict, List, Optional

from core.event_bus import EVENT_AFTER_REPLY, event_bus

# 入库执行摘要的字符上限（保头保尾截断；摘要持久化在 DB，每次窗口加载都计费）
_EXEC_SUMMARY_MAX_CHARS = 4000

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


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

    Plan 收敛不在此处：正常结束由 think_loop 的 ``_finish_round`` 在调用本函数前
    执行 ``tracker.finalize_plan``；异常路径（中断/安全上限）由 think_loop 顶层
    finally 统一收敛（中断 → cancelled，其余 → completed）。
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
                    # 参数值截断：长文本参数（update_skill content 等）全量进摘要
                    # 会让入库历史膨胀（摘要持久化在 DB，每次窗口加载都计费）
                    if len(args_preview) > 200:
                        args_preview = args_preview[:200] + "…"
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
            # 总量截断：多轮长会话的工具摘要可能上万字符，保头保尾 + 中间省略
            summary_body = "\n".join(result_lines)
            if len(summary_body) > _EXEC_SUMMARY_MAX_CHARS:
                head_lines: List[str] = []
                used = 0
                for line in result_lines:
                    if used + len(line) > _EXEC_SUMMARY_MAX_CHARS // 2:
                        break
                    head_lines.append(line)
                    used += len(line)
                tail_lines: List[str] = []
                used = 0
                for line in reversed(result_lines):
                    if used + len(line) > _EXEC_SUMMARY_MAX_CHARS // 2:
                        break
                    tail_lines.append(line)
                    used += len(line)
                omitted = len(result_lines) - len(head_lines) - len(tail_lines)
                summary_body = (
                    "\n".join(head_lines)
                    + f"\n  …（中间 {omitted} 条已省略）…\n"
                    + "\n".join(reversed(tail_lines))
                )
            return (
                f"[已执行操作摘要] 本轮共执行 {len(result_lines)} 次工具\n"
                + summary_body
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

    # 会话用量账本：REPLY 完成 turns+1 并把该会话累计增量落盘
    # （fail-open：统计失败绝不影响回复完成路径）
    _scope = getattr(anything, "entity_scope", "") if anything is not None else ""
    if _scope:
        try:
            from agent.mind.scope_usage import scope_usage_stats
            scope_usage_stats.turn(_scope)
            await scope_usage_stats.flush(_scope)
        except Exception:
            pass  # 统计失败不影响主流程

    # 用户 hook（reply_end）：fire 型事件（记录/通知类脚本挂点）。空配置零开销
    from agent.hooks import hooks_active, run_event_hooks
    if hooks_active("reply_end"):
        try:
            await run_event_hooks(
                "reply_end", scope=_scope,
                iterations=iterations, error=error,
                summary=execution_summary[:400],
            )
        except Exception:
            pass  # hook 失败不影响回复完成

    await event_bus.emit(EVENT_AFTER_REPLY, {
        "scope": _scope,
        "content": content[:100] if content else "",
        "iterations": iterations,
        "error": error,
        "execution_summary": execution_summary,
    })
