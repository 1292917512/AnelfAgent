"""模型输出解析、工具事实配对与截断恢复。"""


from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List

from agent.channel.reply_policy import ReplyToolResult
from agent.context.message_schema import preserve_reasoning_fields
from agent.mind.execution.state import ExecutionContext, RoundOutcome, RoundState
from agent.mind.mind_tools import END_REPLY_TOOL_NAME
from core.log import log
from core.tool_results import extract_error_text, parse_tool_result_json

if TYPE_CHECKING:
    from agent.llm import ChatResult, ToolCall


_MAX_OUTPUT_RECOVERY_LIMIT = 3


_PROMPT_MAX_OUTPUT_CONTINUE = (
    "[系统] 你的上一条输出达到了长度上限被截断。"
    "请直接从中断处继续，不要道歉、不要复述之前的内容；"
    "如果剩余工作较多，请拆分为更小的步骤逐步完成。"
)


def _handle_length_recovery(
        ctx: ExecutionContext,
        state: RoundState,
        result: "ChatResult",
) -> RoundOutcome:
    """max_tokens 截断续写（两级恢复，最多 3 次）。

    截断轮的 tool_calls 参数可能不完整（JSON 断裂），一律丢弃不执行；
    恢复次数耗尽时同样跳过本轮 tool_calls 并提示拆分/结束。
    """

    if getattr(result, "finish_reason", "") != "length":
        return RoundOutcome.PROCEED

    partial_text = _strip_think_blocks(result.content or "").strip()

    if state.max_output_recoveries < _MAX_OUTPUT_RECOVERY_LIMIT:
        state.max_output_recoveries += 1
        log(f"输出被 max_tokens 截断，注入续写提示 (第 {state.max_output_recoveries} 次)",
            "WARNING", tag="思维")
        if partial_text:
            ctx.tool_chain.append({"role": "assistant", "content": partial_text})
        ctx.tool_chain.append({"role": "system", "content": _PROMPT_MAX_OUTPUT_CONTINUE,
                               "_source": {"origin": "length_recovery"}})
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 输出截断，已注入续写提示")
        state.iteration += 1
        return RoundOutcome.CONTINUE

    # 恢复次数耗尽：跳过本轮 tool_calls（参数可能不完整，执行会出错）
    log("输出截断恢复次数耗尽，跳过本轮 tool_calls 并结束", "WARNING", tag="思维")
    if partial_text:
        ctx.tool_chain.append({"role": "assistant", "content": partial_text})
    ctx.tool_chain.append({
        "role": "system",
        "content": "[系统] 输出多次被截断，本轮工具调用参数可能不完整，已跳过执行。"
                   "请拆分为更小的步骤或调用 end_reply 结束。",
        "_source": {"origin": "length_recovery"},
    })
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 截断恢复耗尽，跳过 tool_calls")
    state.iteration += 1
    return RoundOutcome.CONTINUE


def should_end_reply(tool_calls: List["ToolCall"]) -> bool:
    """检测本轮是否应结束：AI 调用了 end_reply。"""
    return any(tc.name == END_REPLY_TOOL_NAME for tc in tool_calls)


_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """剥离 content 中内联的 <think>/<thinking> 推理块。

    推理内容应只走 reasoning_content 独立字段，内联 think 块若留在 content
    会泄漏到对话记录与频道消息中，并膨胀上下文。
    """
    if not text or "<think" not in text.lower():
        return text
    return _THINK_BLOCK_RE.sub("", text).strip()


def resolve_tool_calls(result: "ChatResult") -> List["ToolCall"]:
    """从 LLM 回复中提取工具调用。"""
    if result.tool_calls:
        log(
            f"原生工具调用 {len(result.tool_calls)} 个: "
            f"{', '.join(tc.name for tc in result.tool_calls)}",
            tag="思维",
        )
        return result.tool_calls
    return []


def _detect_token_leak(result: "ChatResult", tool_calls: List["ToolCall"]) -> bool:
    """检测 AI 输出（文本或工具调用参数）是否复述了会话令牌。"""
    from agent.security.session_token import detect_leak
    if result.content and detect_leak(result.content):
        return True
    for tc in tool_calls:
        if tc.arguments and detect_leak(tc.arguments):
            return True
    return False


def _check_tool_results_all_errors(
        tool_chain: List[Dict],
        tool_calls: List["ToolCall"],
) -> bool:
    """检测最近一批工具调用结果是否全部为错误。

    从 tool_chain 末尾查找与本轮 tool_calls 对应的 role=tool 消息，
    判断每条结果是否包含 error 关键信号。全部为错误时返回 True。
    """
    tc_ids = {tc.id for tc in tool_calls}
    if not tc_ids:
        return False

    results: List[str] = []
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            # 已开始收集后遇到非 tool 消息 = 到达轮次边界；
            # 未开始收集时的尾部非 tool 消息是多模态注入的 user 消息，跳过
            if results:
                break
            continue
        if msg.get("tool_call_id") in tc_ids:
            content = msg.get("content", "")
            results.append(content if isinstance(content, str) else "")

    if not results:
        return False

    for r in results:
        parsed = parse_tool_result_json(r)
        if not isinstance(parsed, dict):
            # 非 JSON dict 内容视为非错误（纯文本结果）
            return False
        # 有 error 真值 → 错误，继续检查下一个（{"error": null/""} 视为成功）
        if parsed.get("error"):
            continue
        # success=false / ok=false → 错误，继续检查下一个
        if parsed.get("success") is False or parsed.get("ok") is False:
            continue
        # 无错误信号，至少一个成功
        return False
    # 全部都是错误结果
    return True


_ERROR_BRIEF_MAX = 150


def _collect_round_error_briefs(
        tool_chain: List[Dict],
        tool_calls: List["ToolCall"],
) -> List[str]:
    """收集本轮失败工具的「工具名: 错误摘要」列表。

    遍历规则与 _check_tool_results_all_errors 一致（跳过尾部多模态注入消息）。
    摘要按固定长度截断，供日志快速定位错误来源。
    """
    tc_ids = {tc.id for tc in tool_calls}
    if not tc_ids:
        return []
    tc_names = {tc.id: tc.name for tc in tool_calls}

    briefs: List[str] = []
    matched_any = False
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            if matched_any:
                break
            continue
        tc_id = msg.get("tool_call_id")
        if tc_id not in tc_ids:
            continue
        matched_any = True
        content = msg.get("content", "")
        err = extract_error_text(content if isinstance(content, str) else "")
        if err:
            if len(err) > _ERROR_BRIEF_MAX:
                err = err[:_ERROR_BRIEF_MAX] + "…"
            briefs.append(f"{tc_names.get(tc_id, '?')}: {err}")
    briefs.reverse()  # 恢复调用顺序
    return briefs


def _append_assistant_msg(
        tool_chain: List[Dict],
        result: ChatResult,
        content: str,
) -> None:
    """追加 assistant 消息并保留推理字段（维持多轮思维链连续性）。

    空 content 且无 tool_calls 时不入链：litellm 会对空文本注入占位文本
    常驻历史并被模型复述。content 入链时做孤代理清洗（模型输出是脏字符
    来源之一；发送边界的全量扫描仍是兜底）。
    """
    if not content and not result.tool_calls:
        return
    if content:
        from core.sanitizer import clean_surrogates, has_surrogates
        if has_surrogates(content):
            content = clean_surrogates(content)
    assistant_msg = {"role": "assistant", "content": content}
    preserve_reasoning_fields(assistant_msg, result)
    tool_chain.append(assistant_msg)


def _reply_tool_results(tool_chain: List[Dict]) -> list[ReplyToolResult]:
    """按实际调用 ID 配对已返回事实，不解析模型正文或结束备注。"""
    from core.tool_results import parse_tool_result_json

    names: dict[str, str] = {}
    results: list[ReplyToolResult] = []
    for message in tool_chain:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call.get("id", "")] = call.get("function", {}).get("name", "")
        elif message.get("role") == "tool":
            content = message.get("content")
            results.append(ReplyToolResult(
                names.get(message.get("tool_call_id", ""), ""),
                parse_tool_result_json(content) if isinstance(content, str) else None,
            ))
    return results


_END_REPLY_TEXT_RE = re.compile(r"^end_reply\s*\(.*\)\s*$", re.DOTALL)
