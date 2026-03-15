"""统一思维循环：多轮 LLM 调用 + 原生工具编排。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.event_bus import (
    event_bus,
    EVENT_BEFORE_REPLY,
    EVENT_AFTER_REPLY,
    EVENT_TOOL_EXECUTED,
    EVENT_THINKING_TOOL_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_REPLY_ROUND,
    EVENT_THINKING_FAKE_TOOL_CALL,
)
from core.log import log

if TYPE_CHECKING:
    from agent.core.llm import ChatResult, ImageContent, ToolCall
    from agent.core.messages import Everything
    from agent.core.mind.mind import Mind

_END_REPLY_TOOL_NAME = "end_reply"

# ------------------------------------------------------------------
# 思维循环系统提示常量
# ------------------------------------------------------------------

_PROMPT_TIMEOUT = (
    "[系统通知] 本次 LLM 调用已超时（>{timeout}s），模型可能响应过慢或不可用。\n"
    "请选择以下操作之一：\n"
    "1. 调用 switch_model 切换到响应更快的模型后继续处理\n"
    "2. 调用 end_reply 结束本轮\n"
    "请立即做出选择，不要重复刚才超时的操作。"
)

_PROMPT_FAKE_TOOL_CALL = (
    "[系统拦截] 你上一条回复被拦截，因为你在文本中伪造了工具调用结果。"
    "这些文本不会被执行。你必须通过 function calling 接口发起真正的工具调用。"
    "请立刻使用真正的工具而不是伪造假工具。"
)

_PROMPT_CONTINUE = (
    "[系统提示] 继续执行，若已完成所有操作请调用 end_reply 结束。"
)

_PROMPT_INNER_MONOLOGUE = (
    "[系统提示] 你刚才的文字输出是内心独白，用户看不到！"
    "要回复用户必须调用 send_message 工具。"
    "若已完成所有操作请调用 end_reply 结束。"
)

_PROMPT_EMPTY_OUTPUT = (
    "[系统提示] 你刚才没有执行任何操作也没有输出任何内容。"
    "如果你已完成所有任务，请立即调用 end_reply 结束；"
    "如果还有待处理的事情，请立即使用工具继续操作。"
    "禁止再次输出空内容。"
)


class ThinkMode(str, Enum):
    """思维循环模式。"""

    REPLY = "reply"
    """对话模式：处理用户消息，通过工具发送回复。"""

    REFLECT = "reflect"
    """反思模式：内省思考，收集文本输出，不发送消息。"""


# ==================================================================
# 公共入口
# ==================================================================

async def reply_entry(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
) -> None:
    """执行回复，异常时发送错误提示。"""
    await event_bus.emit(EVENT_BEFORE_REPLY, {"phase": "llm_calling"})
    try:
        await reply_loop(mind, anything, images or [])
    except Exception as exc:
        log(f"reply 异常: {type(exc).__name__}: {exc}", "ERROR", tag="思维")
        error_msg = f"抱歉，处理消息时出错了: {type(exc).__name__}: {exc}"
        await complete_reply(mind, anything, error_msg, 0, error=True)


def collect_pending_images(mind: Mind) -> List[ImageContent]:
    return mind.pfc.collect_images()


def save_base64_image(b64_data: str, mime_type: str = "image/jpeg") -> str:
    """将 base64 图片数据保存为文件，返回路径。"""
    import base64
    import os
    import time as _time
    ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1] if "/" in mime_type else "jpg"
    upload_dir = os.path.abspath(os.path.join("workspace", "uploads", "image"))
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"vision_{int(_time.time() * 1000)}.{ext}"
    fpath = os.path.join(upload_dir, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return fpath


async def apply_vision(
        mind: Mind,
        messages: List[Dict],
        images: List[ImageContent],
        anything: Optional[Everything] = None,
) -> List[Dict]:
    """将图片路径以 [media_file:image:path] 标签注入到对话历史。"""
    if not images:
        return messages

    log(f"processing {len(images)} image(s) as path tags", tag="思维")

    tag_parts: List[str] = []
    for img in images:
        path = img.data
        if not img.is_url and len(path) > 500:
            path = save_base64_image(path, img.mime_type)
        tag_parts.append(f"[media_type:image][media_path:{path}]")

    if not tag_parts:
        return messages

    combined = "\n".join(tag_parts)

    if anything:
        await mind._add_system_context(anything, combined)

    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            c = result[i].get("content", "")
            if isinstance(c, str):
                result[i] = {**result[i], "content": f"{c}\n\n{combined}"}
            break
    return result


# ==================================================================
# 循环主体
# ==================================================================

async def reply_loop(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
) -> None:
    """多轮对话循环入口：处理图片，委托给统一思维循环。"""
    mc = mind._get_mind_config()
    adapter_key = mind._resolve_adapter_key()
    active_tools = mind.pfc.get_active_tool_schemas(adapter_key)
    base_messages = await mind.get_recollection(anything=anything)
    if images:
        base_messages = await apply_vision(mind, base_messages, images, anything)

    try:
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
        )
    finally:
        mind.pfc.clear_dynamic_tools()


async def think_loop(
        mind: Mind,
        mode: ThinkMode,
        tool_chain: List[Dict],
        execution_steps: List[str],
        start_time: float,
        safety_limit: int,
        collected_text: List[str],
        active_tools: List[Dict],
        anything: Optional[Everything] = None,
        base_messages: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
) -> None:
    """统一思维循环：对话和反思共享同一流程。

    通过 mode 参数区分行为：
    - REPLY：处理用户消息，通过工具发送回复，写入对话历史
    - REFLECT：内省思考，收集文本输出到 collected_text，不发送消息

    base_messages 仅首轮获取，后续轮次复用缓存。
    工具集由调用方构建并传入，确保模式差异在入口处理。
    """
    from agent.core.mind.autonomous import MindPhase

    mode_label = "反思" if mode == ThinkMode.REFLECT else "对话"
    adapter_key = mind._resolve_adapter_key() if mode == ThinkMode.REPLY else ""
    if base_messages is None:
        if mode == ThinkMode.REPLY and anything:
            base_messages = await mind.get_recollection(anything=anything)
        else:
            base_messages = []

    iteration = 0
    consecutive_fake_calls = 0
    consecutive_empty_calls = 0

    while iteration < safety_limit:
        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {
            "iteration": iteration,
            "safety_limit": safety_limit,
            "elapsed": time.time() - start_time,
            "steps_so_far": len(execution_steps),
            "mode": mode.value,
        })

        exec_context = mind.pfc.build_execution_context(
            execution_steps, start_time, iteration,
            adapter_key=adapter_key, safety_limit=safety_limit,
            anything=anything,
        )
        llm_messages = [exec_context] + base_messages + tool_chain

        mind._set_phase(MindPhase.LLM_CALLING)
        try:
            result = await mind._invoke_llm_unified(
                llm_messages, active_tools or None, anything, options=options,
            )
        except asyncio.TimeoutError:
            timeout_val = mind._get_mind_config().llm_timeout
            log(f"LLM 调用超时 ({timeout_val}s)，注入恢复提示继续循环", "WARNING", tag="思维")
            execution_steps.append(f"→ 第{iteration + 1}轮: LLM 调用超时 ({timeout_val}s)")
            tool_chain.append({
                "role": "user",
                "content": _PROMPT_TIMEOUT.format(timeout=timeout_val),
            })
            iteration += 1
            continue

        tool_calls = resolve_tool_calls(result)

        if not tool_calls:
            raw_text = (result.content or "").strip()
            is_fake = bool(
                raw_text
                and (
                    raw_text.startswith("[工具执行记录]")
                    or raw_text.startswith("[已执行操作摘要]")
                    or raw_text.startswith("call_function")
                    or ('"success"' in raw_text[:200] and '"action"' in raw_text[:500])
                )
            )

            if is_fake:
                consecutive_fake_calls += 1
                log(
                    f"过滤假工具执行记录 (轮次 {iteration + 1}, "
                    f"连续 {consecutive_fake_calls} 次)",
                    "WARNING", tag="思维",
                )
                await event_bus.emit(
                    EVENT_THINKING_FAKE_TOOL_CALL, {
                        "iteration": iteration + 1,
                        "consecutive": consecutive_fake_calls,
                        "content_preview": raw_text[:200],
                    },
                )
                if consecutive_fake_calls >= 2:
                    log("连续假工具调用过多，强制结束本轮", "WARNING", tag="思维")
                    execution_steps.append(
                        f"→ 第{iteration + 1}轮: 连续假工具调用 {consecutive_fake_calls} 次，强制结束"
                    )
                    if mode == ThinkMode.REPLY and anything:
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                    return

                assistant_msg: Dict[str, Any] = {"role": "assistant", "content": raw_text}
                preserve_reasoning_fields(assistant_msg, result)
                tool_chain.append(assistant_msg)
                tool_chain.append({
                    "role": "user",
                    "content": _PROMPT_FAKE_TOOL_CALL,
                })
                execution_steps.append(f"→ 第{iteration + 1}轮: 假工具调用已拦截并纠正")
            elif raw_text:
                consecutive_fake_calls = 0
                consecutive_empty_calls = 0
                assistant_msg = {"role": "assistant", "content": raw_text}
                preserve_reasoning_fields(assistant_msg, result)
                tool_chain.append(assistant_msg)
                # 追加 user 分隔消息，确保下一轮上下文不以 assistant 结尾，
                # 避免违反 OpenAI/Anthropic 的消息交替规范，防止连续 assistant 消息。
                tool_chain.append({
                    "role": "user",
                    "content": _PROMPT_CONTINUE,
                })
                collected_text.append(raw_text)
                if mode == ThinkMode.REPLY and anything:
                    log(f"内心独白: {raw_text[:100]}", "DEBUG", tag="思维")
                    await save_ai_thought(mind, anything, raw_text)
                    tool_chain[-1] = {
                        "role": "user",
                        "content": _PROMPT_INNER_MONOLOGUE,
                    }
                execution_steps.append(f"→ 第{iteration + 1}轮: {mode_label}中")
            else:
                consecutive_fake_calls = 0
                consecutive_empty_calls += 1
                if result.reasoning_content:
                    assistant_msg = {"role": "assistant", "content": ""}
                    preserve_reasoning_fields(assistant_msg, result)
                    tool_chain.append(assistant_msg)
                execution_steps.append(f"→ 第{iteration + 1}轮: 空输出（思考中）")
                log(f"空输出，继续循环 (轮次 {iteration + 1}, 连续 {consecutive_empty_calls} 次)", "DEBUG", tag="思维")
                if consecutive_empty_calls >= 2:
                    log(f"连续空输出 {consecutive_empty_calls} 次，强制结束本轮", "WARNING", tag="思维")
                    execution_steps.append(f"→ 第{iteration + 1}轮: 连续空输出 {consecutive_empty_calls} 次，强制结束")
                    if mode == ThinkMode.REPLY and anything:
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                    return
                tool_chain.append({
                    "role": "user",
                    "content": _PROMPT_EMPTY_OUTPUT,
                })

            iteration += 1
            continue

        # 有工具调用
        mind._set_phase(MindPhase.TOOL_EXECUTING)
        await execute_tool_calls(mind, tool_chain, result, tool_calls, iteration, anything)
        for tc in tool_calls:
            mind.pfc.record_tool_use(tc.name)
        mind.pfc.expand_discovered_tools(tool_calls)

        tool_names = ", ".join(tc.name for tc in tool_calls)
        execution_steps.append(f"→ 第{iteration + 1}轮: 调用工具 [{tool_names}]")

        if should_end_reply(tool_calls, tool_chain):
            log(f"AI 主动结束{mode_label} (轮次 {iteration + 1})", tag="思维")
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        iteration += 1

    # 达到安全上限
    log(f"达到安全上限 ({safety_limit} 轮)，强制结束", "WARNING", tag="思维")
    if mode == ThinkMode.REPLY and anything:
        await finish_think(mind, anything, execution_steps, safety_limit, tool_chain)


# ==================================================================
# 思维循环辅助方法
# ==================================================================

def should_end_reply(tool_calls: List[ToolCall], tool_chain: List[Dict]) -> bool:
    """检测本轮是否应结束：直接调用 end_reply 或 multi_tool_invoke 内含 end_reply。"""
    if any(tc.name == _END_REPLY_TOOL_NAME for tc in tool_calls):
        return True
    mt_ids = {tc.id for tc in tool_calls if tc.name == "multi_tool_invoke"}
    if mt_ids:
        for msg in reversed(tool_chain):
            if msg.get("role") != "tool":
                continue
            if msg.get("tool_call_id") not in mt_ids:
                continue
            try:
                content = json.loads(msg.get("content", ""))
                if isinstance(content, dict) and content.get("_end_reply"):
                    return True
            except (json.JSONDecodeError, TypeError):
                pass
    return False


def resolve_tool_calls(result: ChatResult) -> List[ToolCall]:
    """从 LLM 回复中提取工具调用。"""
    if result.tool_calls:
        log(
            f"原生工具调用 {len(result.tool_calls)} 个: "
            f"{', '.join(tc.name for tc in result.tool_calls)}",
            tag="思维",
        )
        return result.tool_calls
    return []


async def execute_tool_calls(
        mind: Mind,
        tool_chain: List[Dict],
        result: ChatResult,
        tool_calls: List[ToolCall],
        iteration: int,
        anything: Optional[Everything] = None,
) -> None:
    """执行工具调用并将 assistant + tool 消息追加到 tool_chain。

    保留 content 和推理字段以维持多轮思维链连续性。
    实际发送内容由工具（如 send_message）的 _record_to_context 负责写入 DB。
    """
    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "content": result.content or "",
        "tool_calls": [tc.raw for tc in tool_calls],
    }
    preserve_reasoning_fields(assistant_msg, result)
    tool_chain.append(assistant_msg)
    _tasks = [execute_one_tool(mind, tc, iteration, anything) for tc in tool_calls]
    _outputs = await asyncio.gather(*_tasks, return_exceptions=True)
    for tc, output in zip(tool_calls, _outputs):
        if isinstance(output, BaseException):
            output = json.dumps({"error": str(output)}, ensure_ascii=False)
        tool_chain.append({"role": "tool", "tool_call_id": tc.id, "content": output})
    log_tool_round(iteration, tool_calls)


async def execute_one_tool(
        mind: Mind,
        tc: ToolCall,
        iteration: int,
        anything: Optional[Everything] = None,
) -> str:
    """执行单个工具调用。"""
    from agent.core.mind.autonomous import MindPhase

    mind._set_phase(MindPhase.TOOL_EXECUTING)
    await event_bus.emit(EVENT_TOOL_EXECUTED, {"tool": tc.name, "iteration": iteration})
    await event_bus.emit(EVENT_THINKING_TOOL_START, {
        "tool_name": tc.name,
        "tool_id": tc.id,
        "arguments_preview": tc.arguments[:300] if tc.arguments else "",
        "iteration": iteration,
    })
    log(f"执行工具: {tc.name}", tag="思维")
    t0 = time.time()
    try:
        result = await mind.tool_executor(tc)  # type: ignore[misc]
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "result_preview": result[:300] if result else "",
            "success": True,
        })
        return result
    except Exception as exc:
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "error": str(exc),
            "success": False,
        })
        log(f"工具 {tc.name} 执行失败: {exc}", "WARNING", tag="思维")
        if mind.memory_store:
            try:
                await mind.memory_store.record_tool_error(
                    tool_name=tc.name,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                    args_json=(tc.arguments or "")[:500],
                )
            except Exception:
                pass
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def preserve_reasoning_fields(msg: Dict[str, Any], result: ChatResult) -> None:
    """从 ChatResult.raw 中提取 reasoning_details 到 assistant 消息，维持多轮思维链。

    litellm 统一返回 OpenAI 格式，仅需处理 reasoning_details 字段。
    """
    if not result.raw or not result.reasoning_content:
        return
    try:
        choices = result.raw.get("choices")
        if choices and isinstance(choices, list):
            rd = choices[0].get("message", {}).get("reasoning_details")
            if rd:
                msg["reasoning_details"] = rd
    except (IndexError, AttributeError, TypeError):
        pass


async def save_ai_thought(mind: Mind, anything: Optional[Everything], text: str) -> None:
    """保存 AI 内心独白到对话历史。

    以 role="user" 写入，避免对话历史末尾残留 assistant 消息，
    防止 Anthropic 等模型报 assistant prefill 400 错误。
    """
    if not anything or not text:
        return
    tagged = f"[内心独白] {text}"
    await mind._add_system_context(anything, tagged, role="user")


async def finish_think(
        mind: Mind,
        anything: Everything,
        execution_steps: List[str],
        iterations: int,
        tool_chain: Optional[List[Dict]] = None,
) -> None:
    """思维循环结束处理：工具结果持久化 + 执行摘要写入短期记忆。"""
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
                        args_preview = ", ".join(
                            f"{k}={str(v)[:40]}" for k, v in args_obj.items()
                        )
                    except Exception:
                        args_preview = args_raw[:80]
                    call_map[tc_id] = f"{name}({args_preview})"

        result_lines: List[str] = []
        tool_idx = 0
        for msg in tool_chain:
            if msg.get("role") == "tool":
                tool_idx += 1
                tc_id = msg.get("tool_call_id", "")
                call_sig = call_map.get(tc_id, f"tool#{tool_idx}")
                result = (msg.get("content") or "")[:200]
                result_lines.append(f"  #{tool_idx} {call_sig} → {result}")

        if result_lines:
            await mind._add_system_context(
                anything,
                f"[已执行操作摘要] 本轮共执行 {len(result_lines)} 次工具\n"
                + "\n".join(result_lines),
                role="user",
            )

    if execution_steps:
        summary = f"[上轮执行摘要] 共 {iterations} 轮\n" + "\n".join(execution_steps)
        mind.pfc.add_temporary({"role": "user", "content": summary})

    await complete_reply(mind, anything, "", iterations, tool_chain=tool_chain)


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
) -> None:
    """记录 AI 最终输出，清理回复状态。"""
    from agent.core.mind.autonomous import MindPhase

    mind._set_phase(MindPhase.REPLYING)
    content = (content or "").strip()

    if content:
        adapter_key = getattr(anything, "adapter_key", "") or "unknown"
        log(f"轮次结束: [{adapter_key}] {len(content)} 字符内心独白, 工具轮次={iterations}", "DEBUG", tag="思维")
        await save_ai_thought(mind, anything, content)

    mind._reply_adapter_key = ""

    await event_bus.emit(EVENT_AFTER_REPLY, {
        "content": content[:100] if content else "",
        "iterations": iterations,
        "error": error,
    })


def log_tool_round(iteration: int, tool_calls: List[ToolCall]) -> None:
    log(
        f"第 {iteration + 1} 轮工具调用: "
        f"{', '.join(tc.name for tc in tool_calls)}",
        tag="思维",
    )
