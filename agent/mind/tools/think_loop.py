"""统一思维循环：多轮 LLM 调用 + 原生工具编排。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
import json
import re
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

from agent.mind.tools.result_pipeline import (
    ToolResultPipeline,
    truncate_tool_output as _truncate_tool_output,
)

if TYPE_CHECKING:
    from agent.llm import ChatResult, ImageContent, ToolCall
    from agent.messages import Everything
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind

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

# 端点不接受强制 tool_choice 时的提示词替代约束（API 级强制缺失时的提示级兜底）
_PROMPT_TOOL_OUTPUT_DISCIPLINE = (
    "[输出纪律] 当前模型端点不支持强制工具调用，你必须自觉遵守：\n"
    "1. 回复用户的内容一律调用 send_message 工具发出，直接输出的文字用户完全看不到\n"
    "2. 需要执行动作时立即调用对应工具，禁止只用文字描述动作\n"
    "3. 全部完成后调用 end_reply 结束本轮"
)

_PROMPT_INNER_MONOLOGUE = (
    "[系统提示] 你刚才的文字输出是内心独白，用户看不到！"
    "要回复用户必须调用 send_message 工具。"
    "若已完成所有操作请调用 end_reply 结束。"
)

_PROMPT_INNER_MONOLOGUE_STRICT = (
    "[严重警告] 你已连续多次只输出文字而不调用工具！"
    "文字输出用户完全看不到，等于什么都没做。\n"
    "下一轮你必须二选一，禁止再输出任何普通文字：\n"
    "1. 要回复用户 → 立即调用 send_message\n"
    "2. 没有要说的 → 立即调用 end_reply\n"
    "再次只输出文字将被系统强制结束本轮。"
)

# 连续内心独白上限：达到后强制结束（防模型陷入叙事模式死循环）
_MAX_CONSECUTIVE_MONOLOGUES = 3

_PROMPT_EMPTY_OUTPUT = (
    "[系统提示] 你刚才没有执行任何操作也没有输出任何内容。"
    "如果你已完成所有任务，请立即调用 end_reply 结束；"
    "如果还有待处理的事情，请立即使用工具继续操作。"
    "禁止再次输出空内容。"
)

_PROMPT_TOOL_ERROR_ESCALATION = (
    "[严重警告] 工具调用连续返回错误，你可能陷入了参数格式错误的循环。"
    "请立即停止重试，改用以下策略之一：\n"
    "1. 调用 end_reply 结束本轮\n"
    "2. 换用完全不同的工具或不同的参数格式\n"
    "禁止继续以相同方式调用正在报错的工具。"
)

_PROMPT_END_BLOCKED_FAILURE = (
    "[系统拦截] 结束请求未生效：本轮以下工具执行失败，相关操作未完成：\n"
    "{failures}\n"
    "请根据错误原因修正后重新调用失败的工具"
    "（注意：target_id 等 ID 类参数必须按 schema 声明传字符串类型，不要传数字），"
    "全部成功后再调用 end_reply 结束。若确认无法修复，可再次调用 end_reply 强制结束。"
)

_PROMPT_SECURITY_LEAK = (
    "[系统安全检测] 你的上一条回复中包含了会话安全标记（一次性令牌）。"
    "该标记仅用于标识可信历史，严禁复述。"
    "请不要给出额外解释或道歉，保持原有回复格式重新输出。"
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
    """处理图片：大 base64 图转存为文件路径。

    图片标签已由 add_conversation_record_by_everything 写入用户消息（持久化），
    此处不再重复写 system 消息或追加标签（避免 user/system/内存三处重复）。
    仅当图片是超大 base64 数据时转存为文件，并更新用户消息中的标签路径。
    """
    if not images:
        return messages

    log(f"processing {len(images)} image(s)", tag="思维")

    # 仅处理需要转存的超大 base64 图片（QQ/Telegram 通常是 URL/文件路径，无需处理）
    path_map: Dict[str, str] = {}
    for img in images:
        path = img.data
        if not img.is_url and len(path) > 500:
            path_map[path] = save_base64_image(path, img.mime_type)

    if not path_map:
        return messages

    # 将用户消息中的 base64 标签路径替换为转存后的文件路径
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            c = result[i].get("content", "")
            if isinstance(c, str):
                for old_path, new_path in path_map.items():
                    c = c.replace(f"[media_path:{old_path}]", f"[media_path:{new_path}]")
                result[i] = {**result[i], "content": c}
            break
    return result


# ==================================================================
# 循环主体
# ==================================================================

def _supports_forced_tool_choice(mind: Mind) -> bool:
    """当前 LLM 端点是否接受强制工具选择（tool_choice=required）。"""
    client = getattr(mind, "llm", None)
    config = getattr(client, "config", None)
    return bool(getattr(config, "supports_forced_tool_choice", True))


def _consume_pending_for_scope(mind: Mind, anything: Everything) -> None:
    """消费当前 scope 的待处理队列条目（新消息已并入当前循环，无需另起周期）。"""
    try:
        from agent.messages import EverythingGroup
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            mind.pfc.consume_group_task(anything.group_id)
        else:
            mind.pfc.consume_user_task(anything.uid)
    except Exception as exc:
        log(f"消费待处理队列失败: {exc}", "DEBUG", tag="思维")


async def _fetch_new_user_messages(
        mind: Mind,
        anything: Everything,
        since_ts: int,
) -> List[Dict]:
    """获取循环期间到达的新用户消息（role=user，按时间升序）。

    用于将新消息并入当前 think_loop 上下文，避免图片+文字等连续消息
    被拆成独立周期导致 AI 忘记已回复/丢失上下文关联。
    """
    try:
        scope_type, scope_id = mind._resolve_scope(anything)
        sqlite = mind.conversation_data.router.sqlite
        db = await sqlite._get_db()
        cursor = await db.execute(
            "SELECT role, content, ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? AND ts_ns > ? AND role = 'user' "
            "ORDER BY ts_ns ASC",
            (scope_type, scope_id, int(since_ts)),
        )
        rows = await cursor.fetchall()
        return [{"role": r[0], "content": r[1], "ts_ns": r[2]} for r in rows]
    except Exception as exc:
        log(f"获取新消息失败: {exc}", "DEBUG", tag="思维")
        return []


async def _compress_context(
        mind: Mind,
        base_messages: List[Dict],
        tool_chain: List[Dict],
        scope: str,
) -> tuple[List[Dict], List[Dict]]:
    """执行上下文压缩（保头保尾 + 中间摘要），返回新的 (base_messages, tool_chain)。"""
    return await mind.compressor.compress_messages(
        base_messages, tool_chain,
        scope=scope,
        summarizer=mind.summarize_text,
    )

async def reply_loop(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
) -> None:
    """多轮对话循环入口：处理图片，委托给统一思维循环。"""
    from agent.mind.think_session import think_session

    mc = mind._get_mind_config()
    adapter_key = mind._resolve_adapter_key()
    scope = mind._resolve_entity_scope(anything) if anything else ""
    with think_session(mind, scope):
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
        )


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
    from agent.mind.autonomous import MindPhase
    from agent.mind.guardrails import GuardrailController
    from agent.mind.tool_activation import ToolActivationManager

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
    consecutive_tool_errors = 0
    consecutive_security_leaks = 0
    consecutive_overflow_compressions = 0
    consecutive_monologues = 0
    end_reply_interceptions = 0
    last_prompt_tokens = 0
    # 首次独白后升级为 API 级强制工具调用（tool_choice="required"）
    force_tool_choice = False
    # 纯工具模式：本 Agent 的合法动作（send_message/end_reply/工具）全部是工具调用，
    # 纯文本输出本就不该存在——LLM 调用默认强制工具选择（范式级约束，非事后拦截）
    pure_tool_mode = bool(getattr(mind._get_mind_config(), "force_tool_use", True))
    # 工具调用守卫：跟踪本次会话的调用历史，检测死循环
    guardrail = GuardrailController()
    # 工具结果加工管线：脱敏 → 扫描 → 守卫 → 截断（整轮预算在会话内累计）
    pipeline = ToolResultPipeline(mind, guardrail)
    # 会话期间 scope 不变，循环外解析一次
    current_scope = ToolActivationManager.current_scope()
    # 新消息并入基线：以历史快照水位（快照内最大 ts_ns）为起点，
    # 循环期间到达的用户消息（到达时已实时入库）将并入当前上下文，而非另起周期
    last_merged_ts = time.time_ns()
    if mode == ThinkMode.REPLY and anything:
        try:
            scope_type, scope_id = mind._resolve_scope(anything)
            watermark = mind.conversation_data.get_fetch_watermark(scope_type, scope_id)
            if watermark is not None:
                last_merged_ts = watermark
        except Exception as exc:
            log(f"快照水位获取失败，按当前时间并入: {exc}", "DEBUG", tag="思维")

    while iteration < safety_limit:
        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {
            "iteration": iteration,
            "safety_limit": safety_limit,
            "elapsed": time.time() - start_time,
            "steps_so_far": len(execution_steps),
            "mode": mode.value,
        })

        # 上下文压缩：溢出风险（或手动请求）时压缩中间轮次
        if mind.compressor is not None and mind.compressor.should_compress(
            base_messages + tool_chain,
            last_prompt_tokens=last_prompt_tokens,
            scope=current_scope,
        ):
            base_messages, tool_chain = await _compress_context(
                mind, base_messages, tool_chain, current_scope,
            )
            execution_steps.append(f"→ 第{iteration + 1}轮前: 上下文已压缩")

        # 并入循环期间到达的新用户消息（让 AI 在当前回复中一并处理，
        # 而非另起周期导致上下文断裂/忘记已回复）
        if mode == ThinkMode.REPLY and anything:
            new_msgs = await _fetch_new_user_messages(mind, anything, last_merged_ts)
            if new_msgs:
                for m in new_msgs:
                    tool_chain.append({"role": "user", "content": m["content"]})
                last_merged_ts = new_msgs[-1]["ts_ns"]
                # 消费掉对应的待处理队列条目，避免该消息之后另起独立周期
                _consume_pending_for_scope(mind, anything)
                # 新消息携带的媒体：激活对应媒体工具并重建工具集供后续轮次使用
                # （媒体标签已随内容并入上下文，待处理媒体不留存到后续周期）
                merged_images = mind.pfc.collect_images()
                merged_media = mind.pfc.collect_media()
                if merged_images or merged_media:
                    mind.pfc.activate_media_tools(merged_images, merged_media)
                    active_tools = await mind.pfc.get_active_tool_schemas(
                        adapter_key, scope=mind._resolve_entity_scope(anything),
                    )
                log(f"并入 {len(new_msgs)} 条循环期间新消息到当前上下文", tag="思维")
                execution_steps.append(f"→ 第{iteration + 1}轮前: 并入 {len(new_msgs)} 条新消息")

        exec_context = mind.pfc.build_execution_context(
            execution_steps, start_time, iteration,
            adapter_key=adapter_key, safety_limit=safety_limit,
            anything=anything,
        )
        # 纯工具模式（或独白升级）且有可用工具时，API 级强制工具选择
        require_tools = bool(active_tools) and (pure_tool_mode or force_tool_choice)
        if require_tools and mode == ThinkMode.REPLY and not _supports_forced_tool_choice(mind):
            # 端点（如 thinking 常开的 Kimi）不接受强制 tool_choice，
            # API 级约束缺失，改用提示词约束输出纪律（每轮注入末尾位置）
            exec_context["content"] += "\n" + _PROMPT_TOOL_OUTPUT_DISCIPLINE
        # exec_context（每轮动态）置于末尾：保持 stable/context/volatile/历史前缀
        # 字节稳定供 Prompt Caching 复用，且当前轮状态在模型注意力最强的末尾位置
        llm_messages = base_messages + tool_chain + [exec_context]

        mind._set_phase(MindPhase.LLM_CALLING)
        try:
            result = await mind._invoke_llm_unified(
                llm_messages, active_tools or None, anything,
                tool_choice="required" if require_tools else None,
                options=options,
            )
        except asyncio.TimeoutError:
            timeout_val = mind._get_mind_config().llm_timeout
            log(f"LLM 调用超时 ({timeout_val}s)，注入恢复提示继续循环", "WARNING", tag="思维")
            execution_steps.append(f"→ 第{iteration + 1}轮: LLM 调用超时 ({timeout_val}s)")
            tool_chain.append({
                "role": "system",
                "content": _PROMPT_TIMEOUT.format(timeout=timeout_val),
            })
            iteration += 1
            continue
        except Exception as exc:
            # 上下文超限：立即压缩后重试（连续压缩无效时放弃，防止死循环）
            from agent.llm.error_classifier import classify_llm_error
            classified = classify_llm_error(exc)
            if (
                classified.should_compress
                and mind.compressor is not None
                and consecutive_overflow_compressions < 2
            ):
                consecutive_overflow_compressions += 1
                log(
                    f"LLM 上下文超限，执行紧急压缩 (第 {consecutive_overflow_compressions} 次)",
                    "WARNING", tag="压缩",
                )
                base_messages, tool_chain = await _compress_context(
                    mind, base_messages, tool_chain, current_scope,
                )
                execution_steps.append(f"→ 第{iteration + 1}轮: 上下文超限，已紧急压缩")
                iteration += 1
                continue
            raise

        consecutive_overflow_compressions = 0
        if result.usage and result.usage.prompt_tokens:
            last_prompt_tokens = result.usage.prompt_tokens

        tool_calls = resolve_tool_calls(result)

        # 安全检测：AI 输出复述了会话令牌 → SECURITY 停止，注入纠正提示重试
        if _detect_token_leak(result, tool_calls):
            consecutive_security_leaks += 1
            log(
                f"检测到会话令牌泄露 (轮次 {iteration + 1}, 连续 {consecutive_security_leaks} 次)",
                "WARNING", tag="安全",
            )
            if consecutive_security_leaks >= 2:
                log("连续令牌泄露，强制结束本轮", "WARNING", tag="安全")
                execution_steps.append(f"→ 第{iteration + 1}轮: 连续安全泄露，强制结束")
                if mode == ThinkMode.REPLY and anything:
                    await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                return
            tool_chain.append({"role": "system", "content": _PROMPT_SECURITY_LEAK})
            execution_steps.append(f"→ 第{iteration + 1}轮: 安全泄露已拦截并纠正")
            iteration += 1
            continue
        consecutive_security_leaks = 0

        if not tool_calls:
            raw_text = _strip_think_blocks(result.content or "").strip()
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
                    "role": "system",
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
                    "role": "system",
                    "content": _PROMPT_CONTINUE,
                })
                collected_text.append(raw_text)
                if mode == ThinkMode.REPLY and anything:
                    # 内心独白计数：连续只说不做达到上限时强制结束（防叙事模式死循环）
                    consecutive_monologues += 1
                    log(
                        f"内心独白 (连续 {consecutive_monologues} 次): {raw_text[:100]}",
                        "DEBUG", tag="思维",
                    )
                    # 仅首次独白写入历史（assistant 角色）；
                    # 重复独白不入库，避免历史中的独白模式被模型模仿强化
                    if consecutive_monologues == 1:
                        await save_ai_thought(mind, anything, raw_text)
                    if consecutive_monologues >= _MAX_CONSECUTIVE_MONOLOGUES:
                        log(
                            f"连续内心独白 {consecutive_monologues} 次（只说不做），强制结束本轮",
                            "WARNING", tag="思维",
                        )
                        execution_steps.append(
                            f"→ 第{iteration + 1}轮: 连续内心独白 {consecutive_monologues} 次，强制结束"
                        )
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                        return
                    # 首次独白后升级：下一轮 API 级强制工具调用（不靠劝）
                    force_tool_choice = True
                    tool_chain[-1] = {
                        "role": "system",
                        "content": (
                            _PROMPT_INNER_MONOLOGUE_STRICT
                            if consecutive_monologues >= 2
                            else _PROMPT_INNER_MONOLOGUE
                        ),
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
                    "role": "system",
                    "content": _PROMPT_EMPTY_OUTPUT,
                })

            iteration += 1
            continue

        # 有工具调用
        mind._set_phase(MindPhase.TOOL_EXECUTING)
        consecutive_fake_calls = 0
        consecutive_empty_calls = 0
        consecutive_monologues = 0
        force_tool_choice = False
        await execute_tool_calls(
            mind, tool_chain, result, tool_calls, iteration, anything,
            guardrail=guardrail, pipeline=pipeline,
        )

        # 守卫 halt：同工具连续失败达到上限，强制结束本轮
        if guardrail.halt_decision is not None:
            halt = guardrail.halt_decision
            log(f"工具守卫强制结束: {halt.message}", "WARNING", tag="思维")
            execution_steps.append(f"→ 第{iteration + 1}轮: {halt.message}")
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        # 检测本轮工具结果是否全部为错误
        all_errors = _check_tool_results_all_errors(tool_chain, tool_calls)
        if all_errors:
            consecutive_tool_errors += 1
            log(
                f"全部工具调用返回错误 (轮次 {iteration + 1}, "
                f"连续 {consecutive_tool_errors} 次)",
                "WARNING", tag="思维",
            )
        else:
            consecutive_tool_errors = 0

        for tc in tool_calls:
            mind.pfc.record_tool_use(tc.name)
        mind.pfc.expand_discovered_tools(tool_calls)

        tool_names = ", ".join(tc.name for tc in tool_calls)
        execution_steps.append(f"→ 第{iteration + 1}轮: 调用工具 [{tool_names}]")

        if consecutive_tool_errors >= 3:
            log(
                f"连续 {consecutive_tool_errors} 轮工具全部报错，强制结束本轮",
                "WARNING", tag="思维",
            )
            execution_steps.append(
                f"→ 第{iteration + 1}轮: 连续工具错误 {consecutive_tool_errors} 次，强制结束"
            )
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        # 连续错误达到阈值时注入警告
        if consecutive_tool_errors >= 2:
            tool_chain.append({
                "role": "system",
                "content": _PROMPT_TOOL_ERROR_ESCALATION,
            })

        if should_end_reply(tool_calls, tool_chain):
            # 结束拦截：本轮存在失败工具时，注入失败反馈给 AI 修正机会（最多 2 次防死循环）
            if mode == ThinkMode.REPLY and end_reply_interceptions < 2:
                failure_feedback = _collect_round_failures(tool_chain, tool_calls)
                if failure_feedback:
                    end_reply_interceptions += 1
                    log(
                        f"结束请求被拦截: 本轮存在失败工具 (轮次 {iteration + 1}, "
                        f"第 {end_reply_interceptions} 次拦截)",
                        "WARNING", tag="思维",
                    )
                    tool_chain.append({"role": "system", "content": failure_feedback})
                    execution_steps.append(
                        f"→ 第{iteration + 1}轮: 结束被拦截（存在失败工具），已反馈 AI 修正"
                    )
                    iteration += 1
                    continue
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
    """检测本轮是否应结束：AI 调用了 end_reply。"""
    return any(tc.name == _END_REPLY_TOOL_NAME for tc in tool_calls)


_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """剥离 content 中内联的 <think>/<thinking> 推理块（参考 hermes）。

    推理内容应只走 reasoning_content 独立字段，内联 think 块若留在 content
    会泄漏到对话记录与频道消息中，并膨胀上下文。
    """
    if not text or "<think" not in text.lower():
        return text
    return _THINK_BLOCK_RE.sub("", text).strip()


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


def _detect_token_leak(result: ChatResult, tool_calls: List[ToolCall]) -> bool:
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
        tool_calls: List[ToolCall],
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
            break
        if msg.get("tool_call_id") in tc_ids:
            content = msg.get("content", "")
            results.append(content if isinstance(content, str) else "")

    if not results:
        return False

    for r in results:
        # 尝试 JSON 解析判断
        try:
            parsed = json.loads(r)
            if isinstance(parsed, dict):
                # 有 error 键 → 错误，继续检查下一个
                if "error" in parsed:
                    continue
                # success=false / ok=false → 错误，继续检查下一个
                if parsed.get("success") is False or parsed.get("ok") is False:
                    continue
                # 无错误信号，至少一个成功
                return False
            # 非 dict 的 JSON（list/string/number）视为非错误
            return False
        except (json.JSONDecodeError, TypeError):
            # 非 JSON 内容视为非错误（纯文本结果）
            return False
    # 全部都是错误结果
    return True


def _extract_error_text(payload: Any) -> str:
    """从工具结果 payload（dict 或 JSON 字符串）中提取错误文本，无错误返回空串。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False or payload.get("ok") is False:
        return str(payload.get("error", "") or "未知错误")
    if payload.get("error"):
        return str(payload["error"])
    return ""


def _collect_round_failures(tool_chain: List[Dict], tool_calls: List[ToolCall]) -> str:
    """收集本轮工具结果中的失败项，生成结束拦截反馈。无失败时返回空串。"""
    tc_ids = {tc.id for tc in tool_calls}
    if not tc_ids:
        return ""
    tc_names = {tc.id: tc.name for tc in tool_calls}

    failures: List[str] = []
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            break
        tc_id = msg.get("tool_call_id")
        if tc_id not in tc_ids:
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue

        err = _extract_error_text(parsed)
        if err:
            failures.append(f"{tc_names.get(tc_id, '?')}: {err}")

    if not failures:
        return ""
    lines = "\n".join(f"- {f}" for f in failures)
    return _PROMPT_END_BLOCKED_FAILURE.format(failures=lines)


# 工具结果加工（脱敏/扫描/截断）已迁移至 result_pipeline.py，
# 顶部保留 _truncate_tool_output 向后兼容别名（测试与历史调用方使用）。


async def execute_tool_calls(
        mind: Mind,
        tool_chain: List[Dict],
        result: ChatResult,
        tool_calls: List[ToolCall],
        iteration: int,
        anything: Optional[Everything] = None,
        *,
        guardrail: Optional["GuardrailController"] = None,
        pipeline: Optional["ToolResultPipeline"] = None,
) -> None:
    """执行工具调用并将 assistant + tool 消息追加到 tool_chain。

    保留 content 和推理字段以维持多轮思维链连续性。
    实际发送内容由工具（如 send_message）的 _record_to_context 负责写入 DB。
    结果加工（脱敏/扫描/守卫/截断）由 ToolResultPipeline 统一处理。
    """
    from agent.mind.guardrails import synthetic_block_result

    if pipeline is None:
        pipeline = ToolResultPipeline(mind, guardrail)
    pipeline.begin_turn()

    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "content": _strip_think_blocks(result.content or ""),
        "tool_calls": [tc.raw for tc in tool_calls],
    }
    preserve_reasoning_fields(assistant_msg, result)
    tool_chain.append(assistant_msg)

    # 守卫执行前检查：已知必败/无进展的调用直接返回合成结果，不执行真实工具
    blocked_results: Dict[str, str] = {}
    if guardrail is not None:
        for tc in tool_calls:
            decision = guardrail.before_call(tc.name, tc.arguments or "")
            if decision.should_block:
                blocked_results[tc.id] = synthetic_block_result(decision)
                log(f"工具守卫拦截: {tc.name} ({decision.reason})", "WARNING", tag="思维")

    async def _run_one(tc: ToolCall) -> str:
        if tc.id in blocked_results:
            return blocked_results[tc.id]
        return await execute_one_tool(mind, tc, iteration, anything)

    _tasks = [_run_one(tc) for tc in tool_calls]
    _outputs = await asyncio.gather(*_tasks, return_exceptions=True)
    for tc, output in zip(tool_calls, _outputs):
        if isinstance(output, BaseException):
            output = json.dumps({"error": str(output)}, ensure_ascii=False)
        output_str = output if isinstance(output, str) else str(output)
        final_output = pipeline.process(
            tc.name, tc.arguments or "", output_str,
            skip_guardrail=tc.id in blocked_results,
        )
        tool_chain.append({"role": "tool", "tool_call_id": tc.id, "content": final_output})
    log_tool_round(iteration, tool_calls)


async def execute_one_tool(
        mind: Mind,
        tc: ToolCall,
        iteration: int,
        anything: Optional[Everything] = None,
) -> str:
    """执行单个工具调用。"""
    from agent.mind.autonomous import MindPhase

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

    以 role="assistant" 写入——独白是 AI 自己的输出，用 user 角色会让模型
    误以为独白是用户发来的对话模式并加以模仿（模式强化死循环的根源）。
    末尾 assistant 残留由 build_llm_context 的 prefill 修正统一处理。
    """
    if not anything or not text:
        return
    tagged = f"[内心独白] {text}"
    await mind._add_system_context(anything, tagged, role="assistant")


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
            # 工具执行记录持久化到对话历史（system 角色），
            # 等价于主流 function calling 历史中的 assistant(tool_calls) + tool results。
            # 不再重复写入短期记忆（DB 历史每轮都会加载，避免双重注入）。
            await mind._add_system_context(
                anything,
                f"[已执行操作摘要] 本轮共执行 {len(result_lines)} 次工具\n"
                + "\n".join(result_lines),
                role="system",
            )

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
    from agent.mind.autonomous import MindPhase

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
