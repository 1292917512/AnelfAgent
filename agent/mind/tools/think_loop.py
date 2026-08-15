"""统一思维循环：多轮 LLM 调用 + 原生工具编排。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。

拆分结构（本文件只保留主循环、阶段函数与工具执行块）：
- agent.mind.tools.round_helpers：回合状态（_ThinkRoundState/_ThinkLoopCtx）、
  结果判定守卫、压缩/挂起等循环支撑 helper、会话初始化 _prepare_think_context
- agent.mind.tools.vision：图片处理（apply_vision/base64 转存/多模态结果注入）
- agent.mind.tools.reply_finalize：收尾（finish_think/complete_reply/执行摘要）
  与回复入口（reply_entry/reply_loop）

被外部模块（mind.py）与既有测试引用的名字在本文件再导出，导入路径不变。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, AbstractSet, Any, Dict, List, Optional, Set

from agent.channel.reply_route import (
    deliver_text,
    is_short_ack,
    looks_like_context_leak,
    looks_like_fake_tool_call,
    looks_like_preamble,
    should_suppress,
    target_from_anything,
)
from agent.mind.tools.reply_finalize import (
    complete_reply as complete_reply,
)
from agent.mind.tools.reply_finalize import finish_think
from agent.mind.tools.reply_finalize import (
    reply_entry as reply_entry,
)
from agent.mind.tools.reply_finalize import (
    reply_loop as reply_loop,
)
from agent.mind.tools.result_pipeline import (
    ToolResultPipeline,
    truncate_tool_output,
)
from agent.mind.tools.round_helpers import (
    _END_REPLY_TOOL_NAME,
    _MAX_TOOL_CONCURRENCY,
    _OUTPUT_TOOL_NAMES,
    _PLAN_MANAGEMENT_TOOL_NAMES,
    ThinkMode,
    _check_tool_results_all_errors,
    _collect_round_error_briefs,
    _collect_round_failures,
    _compress_context,
    _detect_token_leak,
    _emit_context_usage,
    _format_running_tasks,
    _format_task_completions,
    _handle_length_recovery,
    _handle_overflow,
    _merge_new_messages,
    _merge_pushes,
    _partition_tool_calls,
    _precompact_flush,
    _prepare_think_context,
    _request_tool_approval,
    _round_output_sent_successfully,
    _StageOutcome,
    _streaming_enabled,
    _strip_think_blocks,
    _suspend_for_background,
    _ThinkLoopCtx,
    _ThinkRoundState,
    resolve_tool_calls,
    should_end_reply,
)
from agent.mind.tools.round_helpers import (
    _extract_error_text as _extract_error_text,
)
from agent.mind.tools.round_helpers import (
    _parse_tool_result_json as _parse_tool_result_json,
)
from agent.mind.tools.round_helpers import (
    _rehydrate_recent_files as _rehydrate_recent_files,
)
from agent.mind.tools.vision import _append_multimodal_result
from agent.mind.tools.vision import (
    apply_vision as apply_vision,
)
from agent.mind.tools.vision import (
    collect_pending_images as collect_pending_images,
)
from agent.mind.tools.vision import (
    save_base64_image as save_base64_image,
)
from core.event_bus import (
    EVENT_THINKING_FAKE_TOOL_CALL,
    EVENT_THINKING_REPLY_ROUND,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    EVENT_TOOL_EXECUTED,
    event_bus,
)
from core.log import log
from core.tool_errors import error_from_exception

if TYPE_CHECKING:
    from agent.llm import ChatResult, ToolCall
    from agent.messages import Everything
    from agent.mind.background_tasks import BackgroundTaskInfo
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind

# 兼容历史私有名（tests/unit/agent/mind/test_result_budget.py 直接引用）
_truncate_tool_output = truncate_tool_output

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
    "[系统拦截] 你在文本中伪造了工具调用结果，这些内容不会被执行也不会发送给用户。"
    "请通过 function calling 发起真正的工具调用；"
    "回复用户请调用 send_message。"
)

_PROMPT_CONTEXT_LEAK = (
    "[系统拦截] 你的上一条输出复述了内部注入的上下文内容（系统提示/工具态势等），"
    "这些内容不会被执行也不会发送给用户。"
    "请直接输出给用户的回复正文，或调用 send_message。"
)

_PROMPT_CONTINUE = (
    "[系统提示] 继续执行，若已完成所有操作请调用 end_reply 结束。"
)

# 输出契约（每轮注入）：正文即发送——先做事再说话，最终只输出一次完整回复。
_PROMPT_REPLY_GUIDE = (
    "[输出契约]\n"
    "1. 先做事再说话：需要查资料或执行操作时，立刻通过 function calling 调用工具；"
    "可并行的独立工具同一轮一并发起。\n"
    "2. 正文即发送：你输出的任何纯文本都会立即投递给用户并结束本轮。"
    "因此未完成承诺的工作前不要输出正文——「我来看看」「让我查一下」这类预告"
    "会被系统拦截；全部完成后，只输出一次完整、自成一体的最终回复。\n"
    "3. 若已用 send_message 回复过，不要再输出纯文本（会结束本轮且不再代发）。\n"
    "4. 无需回复 → 调用 end_reply（参数留空），或整条仅输出 [SILENT]"
)

# 前言守卫：承诺式过渡文本（"我来看看…"）被拦截时的纠正提示
_PROMPT_PREAMBLE_GUARD = (
    "[系统拦截] 你刚才输出的是预告动作的过渡文本，系统会把它当作最终回复发送并结束本轮"
    "——这会导致你承诺的操作不会被执行。\n"
    "请立即调用你提到的工具完成工作；若其实无需调用工具，请直接输出完整的回复内容。"
)

# 查资料等非输出工具后：结果仅自己可见；未完成则继续调工具，完成后再回用户
_PROMPT_AFTER_NON_OUTPUT_TOOLS = (
    "[系统提示] 工具结果仅你可见，不会自动发给用户。\n"
    "若任务尚未完成 → 继续调用工具，不要输出过程话术。\n"
    "若已全部完成 → 输出最终回复正文或调用 send_message；"
    "无需回复则调用 end_reply（参数留空）。"
)

# 反思模式的输出纪律（无 send_message，产出文本即反思结果，但动作必须走工具）
_PROMPT_REFLECT_OUTPUT_DISCIPLINE = (
    "[输出纪律] 你必须严格遵守：\n"
    "1. 需要执行动作（检索/查询/分析）时立即调用对应工具，禁止只用文字描述动作\n"
    "2. 文字输出只是思考草稿，不会执行任何操作\n"
    "3. 完成分析后调用 end_reply 结束本轮反思"
)

# 挂起等待超时后的降级提示（任务仍在运行，决策权交还 AI）
_PROMPT_TASKS_STILL_RUNNING = (
    "[系统提示] 你等待的后台任务仍未完成（运行中：{tasks}）。\n"
    "请选择：\n"
    "1. 调用 check_background_tasks 查看最新进度\n"
    "2. 调用 end_reply 结束本轮（任务完成时系统会自动通知你并触发新一轮回复）\n"
    "3. 继续处理其他事务"
)

# 反思模式连续纯文本上限：达到后收束（产出已累积在 collected_text）
_MAX_REFLECT_TEXT_ROUNDS = 3

_PROMPT_TOOL_ERROR_ESCALATION = (
    "[严重警告] 工具调用连续返回错误，你可能陷入了参数格式错误的循环。"
    "请立即停止重试，改用以下策略之一：\n"
    "1. 调用 end_reply 结束本轮\n"
    "2. 换用完全不同的工具或不同的参数格式\n"
    "禁止继续以相同方式调用正在报错的工具。"
)

_PROMPT_SECURITY_LEAK = (
    "[系统安全检测] 你的上一条回复中包含了会话安全标记（一次性令牌）。"
    "该标记仅用于标识可信历史，严禁复述。"
    "请不要给出额外解释或道歉，保持原有回复格式重新输出。"
)


# ==================================================================
# 循环主体
# ==================================================================

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
        *,
        adapter_key: str = "",
        blocked_tools: Optional[Set[str]] = None,
) -> None:
    """统一思维循环：对话和反思共享同一流程。

    通过 mode 参数区分行为：
    - REPLY：处理用户消息，通过工具发送回复，写入对话历史
    - REFLECT：内省思考，收集文本输出到 collected_text，不发送消息

    base_messages 仅首轮获取，后续轮次复用缓存。
    工具集由调用方构建并传入，确保模式差异在入口处理。
    blocked_tools 为模式级禁用工具：schema 保留在数组中（跨调用前缀一致），
    执行侧拦截返回合成错误结果（可见性与权限分离）。
    主循环只保留：中断检查 → LLM 调用 → 分发到阶段函数 → 终止条件判断。
    """
    ctx, state = await _prepare_think_context(
        mind, mode, tool_chain, execution_steps,
        collected_text, active_tools, anything, base_messages,
        options, adapter_key, blocked_tools,
    )

    # 工具数组顺序由 ToolAssembly 跨回复追加式冻结（见 tool_assembly），
    # 会话内/回复间均字节稳定，无需在此再冻结。

    try:
        await _run_think_rounds(ctx, state, safety_limit, start_time)
    finally:
        # plan 全退出路径唯一收敛点：正常结束已由 _finish_round 收敛（finalized
        # 置位）；中断 / 安全上限等异常退出在此收敛——中断 → cancelled，其余 →
        # completed。无 active plan 时 finalize_plan 零成本，幂等安全。
        if not state.plan_finalized:
            try:
                from agent.planning import tracker as _plan_tracker
                await _plan_tracker.finalize_plan(
                    ctx.current_scope,
                    "cancelled" if state.interrupted else "completed",
                )
            except Exception:
                pass  # 收敛失败不影响主流程


async def _run_think_rounds(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        safety_limit: int,
        start_time: float,
) -> None:
    """轮次主循环：中断检查 → LLM 调用 → 阶段分发 → 终止判断。"""
    from agent.mind.autonomous import MindPhase
    from agent.mind.tool_activation import tool_activation as _tool_act_mgr
    from core.entity import EntityRegistry

    mind = ctx.mind
    anything = ctx.anything
    mode = ctx.mode
    execution_steps = ctx.execution_steps

    # 工具集版本快照：每轮检查版本变化，变了就重建 active_tools（保持 prefix 缓存友好）。
    # registry 版本覆盖运行期注册表增删（MCP 热同步/reload/entities 重载/WebUI 开关）——
    # 否则粘性激活的 stay_awake 服务在回复进行中新增工具，要等下一个版本事件才可见
    last_tools_version = (
        getattr(mind.pfc, "tools_version", 0),
        _tool_act_mgr.version,
        EntityRegistry.version(),
    )

    while state.iteration < safety_limit:
        # 中断检查点（协作式）：用户/守卫请求中断时安全收束
        if await _handle_interrupt(ctx, state):
            return

        # 工具集版本检查：激活/发现/注册表变化时重建 active_tools
        # （ToolAssembly 追加式冻结保证重建结果前缀字节稳定，prefix 缓存友好）
        cur_tools_version = (
            getattr(mind.pfc, "tools_version", 0),
            _tool_act_mgr.version,
            EntityRegistry.version(),
        )
        if cur_tools_version != last_tools_version:
            last_tools_version = cur_tools_version
            ctx.active_tools = await mind.pfc.get_active_tool_schemas(
                ctx.adapter_key, scope=ctx.current_scope,
            )
            log(f"工具集版本变化，重建 active_tools: {len(ctx.active_tools)} 个", "DEBUG", tag="思维")

        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {
            "iteration": state.iteration,
            "safety_limit": safety_limit,
            "elapsed": time.time() - start_time,
            "steps_so_far": len(execution_steps),
            "mode": mode.value,
        })

        # Microcompact：完整压缩前的轻量清理（旧只读工具结果 → 占位符）
        if mind.compressor is not None:
            mind.compressor.microcompact(ctx.tool_chain)

        # 上下文压缩：溢出风险（或手动请求）时压缩中间轮次
        if mind.compressor is not None and mind.compressor.should_compress(
            ctx.base_messages + ctx.tool_chain,
            last_prompt_tokens=state.last_prompt_tokens,
            scope=ctx.current_scope,
        ):
            try:
                async with mind.compressor.scope_lock(ctx.current_scope):
                    # PreCompact flush：压缩前把该会话的待定对话抢跑沉淀为
                    # 长期记忆，防止压缩摘要丢弃未提取的细节（信息不失帧）；
                    # fail-open，提取失败/超时绝不阻断压缩
                    if await _precompact_flush(mind, ctx.current_scope):
                        execution_steps.append(
                            f"→ 第{state.iteration + 1}轮前: 压缩前已沉淀待定记忆"
                        )
                    ctx.base_messages, ctx.tool_chain = await _compress_context(
                        mind, ctx.base_messages, ctx.tool_chain, ctx.current_scope,
                        tools=ctx.active_tools,
                    )
            except Exception as exc:
                # 估算/手动触发的压缩失败不应杀死整轮回复：
                # 本轮不压缩继续（硬溢出路径由 _handle_overflow 另行处理，保留原语义）
                log(f"上下文压缩失败，本轮不压缩继续: {exc}", "WARNING", tag="压缩")
            else:
                # 压缩后旧真用量已失真，清零避免下轮以过期值重复触发压缩
                state.last_prompt_tokens = 0
                execution_steps.append(f"→ 第{state.iteration + 1}轮前: 上下文已压缩")

        # 并入循环期间到达的新用户消息（让 AI 在当前回复中一并处理，
        # 而非另起周期导致上下文断裂/忘记已回复）
        await _merge_new_messages(ctx, state)
        # 并入循环期间到达的实体推送（[push:] 系统通知，轮内弹窗）
        await _merge_pushes(ctx, state)

        exec_context = mind.pfc.build_execution_context(
            execution_steps, start_time, state.iteration,
            adapter_key=ctx.adapter_key, safety_limit=safety_limit,
            anything=anything,
        )
        # 纯工具模式（可选）且有可用工具时，API 级强制工具选择
        require_tools = bool(ctx.active_tools) and ctx.pure_tool_mode
        # 输出方式说明随执行上下文每轮注入
        exec_context["content"] += "\n" + (
            _PROMPT_REPLY_GUIDE if mode == ThinkMode.REPLY
            else _PROMPT_REFLECT_OUTPUT_DISCIPLINE
        )
        # exec_context（每轮动态）置于末尾：保持 stable/context/volatile/历史前缀
        # 字节稳定供 Prompt Caching 复用，且当前轮状态在模型注意力最强的末尾位置
        # （缓存断点不在此注入——发送边界由 llm/prompt_cache 按 _layer 统一装饰，
        # 链尾锚点天然随链增长前移）
        llm_messages = ctx.base_messages + ctx.tool_chain + [exec_context]

        mind._set_phase(MindPhase.LLM_CALLING)
        # 超时/上下文超限已在 _invoke_llm_round 内注入恢复提示或紧急压缩
        result = await _invoke_llm_round(ctx, state, llm_messages, require_tools)
        if result is None:
            continue

        state.consecutive_overflow_compressions = 0
        if result.usage and result.usage.prompt_tokens:
            state.last_prompt_tokens = result.usage.prompt_tokens
        if result.usage:
            state.last_cache_read_tokens = result.usage.cache_read_input_tokens
            state.last_cache_creation_tokens = result.usage.cache_creation_input_tokens
            state.last_cache_hit_rate = result.usage.cache_hit_rate

        # 上下文用量快照（usage 锚定：API 真实用量优先；供 webui 状态栏显示）
        await _emit_context_usage(ctx, state)

        # max_output_tokens 截断恢复：长度截断时注入续写提示
        if _handle_length_recovery(ctx, state, result) is _StageOutcome.CONTINUE:
            continue

        tool_calls = resolve_tool_calls(result)

        # 安全检测：AI 输出复述了会话令牌 → 注入纠正提示重试
        outcome = await _handle_security_leak(ctx, state, result, tool_calls)
        if outcome is _StageOutcome.BREAK:
            return
        if outcome is _StageOutcome.CONTINUE:
            continue

        if not tool_calls:
            outcome = await _handle_text_only_round(ctx, state, result)
        else:
            outcome = await _handle_tool_round(ctx, state, result, tool_calls)
        if outcome is _StageOutcome.BREAK:
            return

    # 达到安全上限
    log(f"达到安全上限 ({safety_limit} 轮)，强制结束", "WARNING", tag="思维")
    if mode == ThinkMode.REPLY and anything:
        await finish_think(mind, anything, execution_steps, safety_limit, ctx.tool_chain)


# ==================================================================
# 阶段函数
# ==================================================================

async def _handle_interrupt(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> bool:
    """循环顶部的协作式中断检查；已中断则安全收束并返回 True。

    不发半截消息、不写残缺工具链、历史留中断元消息。
    """
    if not (
        ctx.current_scope
        and ctx.interrupts is not None
        and ctx.interrupts.is_requested(ctx.current_scope)
    ):
        return False
    reason = ctx.interrupts.consume(ctx.current_scope) or "未说明"
    state.interrupted = True  # finally 收敛 plan 时按 cancelled 处理
    log(
        f"会话被中断 (轮次 {state.iteration + 1}): "
        f"scope={ctx.current_scope} reason={reason}",
        tag="中断",
    )
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮前: 会话被中断 ({reason})")
    if ctx.mode == ThinkMode.REPLY and ctx.anything:
        await ctx.mind._add_system_context(
            ctx.anything,
            f"[系统] 本次回复在执行中被中断（{reason}），"
            "未完成的操作已放弃，如需继续请重新发起。",
            role="system",
        )
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration, ctx.tool_chain,
        )
    return True


async def _invoke_llm_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        llm_messages: List[Dict],
        require_tools: bool,
) -> Optional[ChatResult]:
    """单次 LLM 调用；超时/上下文超限已处理（注入提示或紧急压缩）时返回 None。

    替身 Mind（测试/子代理）可能仍是非流式签名：按探测结果按需传流式参数。
    """
    mind = ctx.mind
    try:
        stream_kwargs = (
            {"stream": _streaming_enabled(), "on_delta": ctx.delta_emitter}
            if ctx.supports_stream else {}
        )
        purpose_kwargs = {"purpose": ctx.mode.value} if ctx.supports_purpose else {}
        return await mind._invoke_llm_unified(
            llm_messages, ctx.active_tools or None, ctx.anything,
            tool_choice="required" if require_tools else None,
            options=ctx.options,
            **purpose_kwargs,
            **stream_kwargs,
        )
    except asyncio.TimeoutError:
        timeout_val = mind._get_mind_config().llm_timeout
        log(f"LLM 调用超时 ({timeout_val}s)，注入恢复提示继续循环", "WARNING", tag="思维")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: LLM 调用超时 ({timeout_val}s)")
        ctx.tool_chain.append({
            "role": "system",
            "content": _PROMPT_TIMEOUT.format(timeout=timeout_val),
            "_source": {"origin": "timeout_recovery"},
        })
        state.iteration += 1
        return None
    except Exception as exc:
        # 上下文超限：立即压缩后重试（连续压缩无效时放弃，防止死循环）
        if await _handle_overflow(ctx, state, exc):
            return None
        raise


def _append_assistant_msg(
        tool_chain: List[Dict],
        result: ChatResult,
        content: str,
) -> None:
    """追加 assistant 消息并保留推理字段（维持多轮思维链连续性）。

    空 content 且无 tool_calls 时不入链：litellm 会对空文本注入占位文本
    常驻历史并被模型复述。
    """
    if not content and not result.tool_calls:
        return
    assistant_msg = {"role": "assistant", "content": content}
    preserve_reasoning_fields(assistant_msg, result)
    tool_chain.append(assistant_msg)


async def _finish_round(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> None:
    """正常结束的统一收尾：plan 收敛（全模式）+ REPLY 摘要入库/完成事件。

    - plan 收敛：REPLY / REFLECT 正常结束都执行，scope 取自 ContextVar
    （``ctx.current_scope``），tracker 只处理当前 scope 的 active plan，无 plan 零成本。
    收敛成功后置位 ``state.plan_finalized``，think_loop 的 finally 不再重复收敛。
    - finish_think：仅 REPLY（摘要入库 + complete_reply 需要 anything）。
    异常路径（中断/安全上限）不走这里，由 think_loop 的 finally 统一收敛。
    """
    try:
        from agent.planning import tracker as _plan_tracker
        await _plan_tracker.finalize_plan(ctx.current_scope)
        state.plan_finalized = True
    except Exception:
        pass  # 收敛失败不影响主流程，finally 兜底重试
    if ctx.mode == ThinkMode.REPLY and ctx.anything:
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration + 1, ctx.tool_chain,
        )


async def _handle_security_leak(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
) -> _StageOutcome:
    """安全泄露处理：AI 输出复述了会话令牌 → 注入纠正提示重试，连续 2 次强制结束。"""
    if not _detect_token_leak(result, tool_calls):
        state.consecutive_security_leaks = 0
        return _StageOutcome.PROCEED

    state.consecutive_security_leaks += 1
    log(
        f"检测到会话令牌泄露 (轮次 {state.iteration + 1}, "
        f"连续 {state.consecutive_security_leaks} 次)",
        "WARNING", tag="安全",
    )
    if state.consecutive_security_leaks >= 2:
        log("连续令牌泄露，强制结束本轮", "WARNING", tag="安全")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 连续安全泄露，强制结束")
        await _finish_round(ctx, state)
        return _StageOutcome.BREAK
    ctx.tool_chain.append({"role": "system", "content": _PROMPT_SECURITY_LEAK})
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 安全泄露已拦截并纠正")
    state.iteration += 1
    return _StageOutcome.CONTINUE


async def _handle_text_only_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
) -> _StageOutcome:
    """纯文本轮：无工具调用时的分发。

    分支：假工具调用/注入上下文复述拦截 / 空输出 / [SILENT] 沉默 / 后台任务等待挂起 /
    plan 守卫 / 输出工具后纯文本跳发 / 最终回复投递 / 反思模式收束。
    """
    mind = ctx.mind
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    raw_text = _strip_think_blocks(result.content or "").strip()

    context_leaked = looks_like_context_leak(raw_text)
    if context_leaked or looks_like_fake_tool_call(raw_text):
        # 复述注入上下文 / 伪造工具调用的文本：不投递，提示纠正，连续 2 次强制结束
        intercept_kind = "注入上下文复述" if context_leaked else "假工具调用"
        state.consecutive_fake_calls += 1
        log(
            f"过滤{intercept_kind}文本 (轮次 {state.iteration + 1}, "
            f"连续 {state.consecutive_fake_calls} 次)",
            "WARNING", tag="思维",
        )
        await event_bus.emit(
            EVENT_THINKING_FAKE_TOOL_CALL, {
                "iteration": state.iteration + 1,
                "consecutive": state.consecutive_fake_calls,
                "content_preview": raw_text[:200],
            },
        )
        if state.consecutive_fake_calls >= 2:
            log(f"连续{intercept_kind}过多，强制结束本轮", "WARNING", tag="思维")
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 连续{intercept_kind} "
                f"{state.consecutive_fake_calls} 次，强制结束"
            )
            await _finish_round(ctx, state)
            return _StageOutcome.BREAK

        _append_assistant_msg(tool_chain, result, raw_text)
        tool_chain.append({
            "role": "system",
            "content": (
                _PROMPT_CONTEXT_LEAK if context_leaked else _PROMPT_FAKE_TOOL_CALL
            ),
        })
        execution_steps.append(f"→ 第{state.iteration + 1}轮: {intercept_kind}已拦截并纠正")
    elif not raw_text:
        # 空输出：可接受（思考中/无意回复），不注入纠正提示；
        # 连续 2 次空输出安静结束本轮
        state.consecutive_fake_calls = 0
        state.consecutive_empty_calls += 1
        if result.reasoning_content:
            _append_assistant_msg(tool_chain, result, "")
        execution_steps.append(f"→ 第{state.iteration + 1}轮: 空输出（思考中）")
        log(
            f"空输出，继续循环 (轮次 {state.iteration + 1}, "
            f"连续 {state.consecutive_empty_calls} 次)",
            "DEBUG", tag="思维",
        )
        if state.consecutive_empty_calls >= 2:
            log(f"连续空输出 {state.consecutive_empty_calls} 次，结束本轮", "DEBUG", tag="思维")
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 连续空输出 {state.consecutive_empty_calls} 次，结束"
            )
            await _finish_round(ctx, state)
            return _StageOutcome.BREAK
    elif ctx.mode == ThinkMode.REPLY and should_suppress(raw_text):
        # [SILENT] 精确匹配 / 幻觉沉默旁白：AI 决定不回复，不投递，直接结束本轮
        log(f"AI 选择沉默（{raw_text[:30]}），结束本轮", "DEBUG", tag="思维")
        _append_assistant_msg(tool_chain, result, raw_text)
        execution_steps.append(f"→ 第{state.iteration + 1}轮: AI 选择沉默，结束")
        await _finish_round(ctx, state)
        return _StageOutcome.BREAK
    else:
        state.consecutive_fake_calls = 0
        state.consecutive_empty_calls = 0
        _append_assistant_msg(tool_chain, result, raw_text)
        ctx.collected_text.append(raw_text)

        running_bg: List[BackgroundTaskInfo] = []
        if ctx.mode == ThinkMode.REPLY and ctx.anything and ctx.background is not None:
            running_bg = ctx.background.running(ctx.current_scope)

        if running_bg and state.wait_budget > 0:
            # 等待挂起：后台任务运行中时的纯文本一律视为等待——挂起会合
            # （结构性判定，不解析文本语义）。
            # 挂起期间新消息照常实时入库，中断/新消息/完成/超时都会安全唤醒；
            # 超时说明等待无望，清零预算，后续纯文本回落到普通投递路径。
            reason, completions, elapsed = await _suspend_for_background(
                mind, ctx.anything, ctx.background, ctx.current_scope,
                state.last_merged_ts, min(ctx.wait_per_round, state.wait_budget), ctx.interrupts,
                since_id=state.last_merged_id,
            )
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 等待后台任务（{reason}，{elapsed:.0f}s）"
            )
            if reason == "completed":
                state.wait_budget -= elapsed
                tool_chain.append({
                    "role": "system",
                    "content": _format_task_completions(
                        completions, ctx.background.running(ctx.current_scope),
                    ),
                    "_source": {"origin": "background_task"},
                })
            elif reason == "timeout":
                state.wait_budget = 0.0
                tool_chain.append({
                    "role": "system",
                    "content": _PROMPT_TASKS_STILL_RUNNING.format(
                        tasks=_format_running_tasks(running_bg),
                    ),
                    "_source": {"origin": "background_task"},
                })
            # interrupted：不追加提示，循环顶部统一并入新消息 / 处理中断
            state.iteration += 1
            return _StageOutcome.CONTINUE

        if ctx.mode == ThinkMode.REPLY and ctx.anything:
            # Plan 守卫：有未完成 plan 时，纯文本不当最终回复——
            # 否则 AI 只是"说"了计划/过程话，循环就结束了，任务一步未做。
            # 参考 hermes todo reminder：程序级 nudge，上限 2 次防死循环。
            if state.plan_text_guard_count < 2:
                from agent.planning import tracker as _plan_tracker
                guard_feedback = await _plan_tracker.guard_feedback_for_text_only(ctx.current_scope)
                if guard_feedback:
                    state.plan_text_guard_count += 1
                    log(
                        f"纯文本结束被 plan 守卫拦截: 计划未执行 "
                        f"(轮次 {state.iteration + 1}, 第 {state.plan_text_guard_count} 次)",
                        "WARNING", tag="思维",
                    )
                    tool_chain.append({"role": "system", "content": guard_feedback})
                    execution_steps.append(
                        f"→ 第{state.iteration + 1}轮: 纯文本被拦截（计划未执行），已提醒 AI 调工具"
                    )
                    state.iteration += 1
                    return _StageOutcome.CONTINUE

            # send_message（仅输出类）成功后紧跟纯文本：消息已发出。
            # 短确认（"已发送"类）丢弃防重复出站；实质内容作为跟进消息投递，不再静默丢失。
            if state.prev_round_outbound_only:
                if is_short_ack(raw_text):
                    execution_steps.append(
                        f"→ 第{state.iteration + 1}轮: 输出类工具后短确认跳过投递，本轮结束"
                    )
                    log(
                        "输出类工具后出现短确认文本，跳过代发以防重复出站",
                        "DEBUG", tag="思维",
                    )
                    await _finish_round(ctx, state)
                    return _StageOutcome.BREAK
                follow_target = target_from_anything(ctx.anything, ctx.adapter_key)
                if follow_target is not None:
                    await deliver_text(follow_target, raw_text)
                    execution_steps.append(
                        f"→ 第{state.iteration + 1}轮: 输出类工具后实质文本已跟进投递，本轮结束"
                    )
                else:
                    execution_steps.append(
                        f"→ 第{state.iteration + 1}轮: 输出类工具后纯文本无投递目标，本轮结束"
                    )
                await _finish_round(ctx, state)
                return _StageOutcome.BREAK

            # 前言守卫：承诺式过渡文本（"我来看看…"）一经投递即终态，
            # 会导致"只说不做"。拦截并纠正，上限 2 次防死循环；
            # 被拦的前言暂存，终态投递时合并进最终回复（不丢表达、不轰炸）。
            if state.preamble_guard_count < 2 and looks_like_preamble(raw_text):
                state.preamble_guard_count += 1
                state.preamble_parts.append(raw_text)
                log(
                    f"纯文本结束被前言守卫拦截: 承诺式过渡文本 "
                    f"(轮次 {state.iteration + 1}, 第 {state.preamble_guard_count} 次)",
                    "WARNING", tag="思维",
                )
                tool_chain.append({"role": "system", "content": _PROMPT_PREAMBLE_GUARD})
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 预告文本被拦截，已提醒 AI 先调工具"
                )
                state.iteration += 1
                return _StageOutcome.CONTINUE

            # Hermes 终态：无工具正文 = 最终回复，默认投递回来源会话。
            # 其他会话由各自的 REPLY 周期处理；跨会话发送走 switch_session/send_message。
            # 有被拦截的前言时合并为一条消息投递（保持表达完整且不产生多条出站）。
            target = target_from_anything(ctx.anything, ctx.adapter_key)
            if target is not None:
                deliver_content = (
                    "\n".join([*state.preamble_parts, raw_text])
                    if state.preamble_parts
                    else raw_text
                )
                sent = await deliver_text(target, deliver_content)
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 纯文本已投递到 "
                    f"{target.session_key}，本轮结束"
                    if sent
                    else (
                        f"→ 第{state.iteration + 1}轮: 纯文本投递失败"
                        f"（{target.session_key}），本轮结束"
                    )
                )
            else:
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 纯文本无投递目标，本轮结束"
                )
            await _finish_round(ctx, state)
            return _StageOutcome.BREAK
        else:
            # 反思模式：连续纯文本达到上限即收束（产出已累积在 collected_text）
            state.reflect_text_rounds += 1
            if state.reflect_text_rounds >= _MAX_REFLECT_TEXT_ROUNDS:
                log(
                    f"反思连续纯文本 {state.reflect_text_rounds} 次，结束本轮反思",
                    "WARNING", tag="思维",
                )
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 反思连续纯文本 {state.reflect_text_rounds} 次，结束"
                )
                await _finish_round(ctx, state)
                return _StageOutcome.BREAK
            tool_chain.append({"role": "system", "content": _PROMPT_CONTINUE})
            execution_steps.append(f"→ 第{state.iteration + 1}轮: {ctx.mode_label}中")

    state.iteration += 1
    return _StageOutcome.CONTINUE


async def _handle_tool_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
) -> _StageOutcome:
    """工具执行轮：执行工具批次、全错升级、end_reply 结束拦截与 plan 自动推进。"""
    from agent.mind.autonomous import MindPhase

    mind = ctx.mind
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    guardrail = ctx.guardrail

    mind._set_phase(MindPhase.TOOL_EXECUTING)
    state.consecutive_fake_calls = 0
    state.consecutive_empty_calls = 0
    state.reflect_text_rounds = 0
    await execute_tool_calls(
        mind, tool_chain, result, tool_calls, state.iteration, ctx.anything,
        guardrail=guardrail, pipeline=ctx.pipeline, blocked_tools=ctx.blocked_tools,
    )

    # 记录目标工具使用（goal nag 提醒的计数依据）
    try:
        from agent.planning.nag import note_tools_used
        note_tools_used(ctx.current_scope, [tc.name for tc in tool_calls])
    except Exception:
        pass

    # 守卫 halt：同工具连续失败达到上限，强制结束本轮
    if guardrail.halt_decision is not None:
        halt = guardrail.halt_decision
        log(f"工具守卫强制结束: {halt.message}", "WARNING", tag="思维")
        execution_steps.append(f"→ 第{state.iteration + 1}轮: {halt.message}")
        await _finish_round(ctx, state)
        return _StageOutcome.BREAK

    # 检测本轮工具结果是否全部为错误
    all_errors = _check_tool_results_all_errors(tool_chain, tool_calls)
    if all_errors:
        state.consecutive_tool_errors += 1
        briefs = _collect_round_error_briefs(tool_chain, tool_calls)
        detail = "; ".join(briefs) if briefs else "工具返回失败但未提供错误详情"
        log(
            f"本轮工具调用全部失败 (轮次 {state.iteration + 1}, "
            f"连续 {state.consecutive_tool_errors} 轮): {detail}",
            "WARNING", tag="思维",
        )
    else:
        state.consecutive_tool_errors = 0

    for tc in tool_calls:
        mind.pfc.record_tool_use(tc.name)
    mind.pfc.expand_discovered_tools(tool_calls)

    tool_names = ", ".join(tc.name for tc in tool_calls)
    execution_steps.append(f"→ 第{state.iteration + 1}轮: 调用工具 [{tool_names}]")

    # 标记「仅输出类且已成功」：供下一拍纯文本决定是否跳过代发
    called = {tc.name for tc in tool_calls}
    state.prev_round_outbound_only = bool(
        called
        and called <= _OUTPUT_TOOL_NAMES
        and _round_output_sent_successfully(tool_chain, tool_calls)
    )

    # 非输出工具后：提醒结果仅自己可见（输出类工具已直接发往用户，无需再确认）
    if ctx.mode == ThinkMode.REPLY:
        if (
            not (called & _OUTPUT_TOOL_NAMES)
            and _END_REPLY_TOOL_NAME not in called
        ):
            tool_chain.append({
                "role": "system",
                "content": _PROMPT_AFTER_NON_OUTPUT_TOOLS,
            })

    if state.consecutive_tool_errors >= 3:
        log(
            f"连续 {state.consecutive_tool_errors} 轮工具调用全部失败，强制结束本轮",
            "WARNING", tag="思维",
        )
        execution_steps.append(
            f"→ 第{state.iteration + 1}轮: 连续 {state.consecutive_tool_errors} 轮工具全部失败，强制结束"
        )
        await _finish_round(ctx, state)
        return _StageOutcome.BREAK

    # 连续错误达到阈值时注入警告
    if state.consecutive_tool_errors >= 2:
        tool_chain.append({
            "role": "system",
            "content": _PROMPT_TOOL_ERROR_ESCALATION,
        })

    if should_end_reply(tool_calls):
        # 结束拦截：本轮存在失败工具时注入反馈给 AI 修正机会（最多 2 次防死循环）
        if ctx.mode == ThinkMode.REPLY and state.end_reply_interceptions < 2:
            feedback = _collect_round_failures(tool_chain, tool_calls)
            if feedback:
                state.end_reply_interceptions += 1
                log(
                    f"结束请求被拦截: 存在未完成操作 (轮次 {state.iteration + 1}, "
                    f"第 {state.end_reply_interceptions} 次拦截)",
                    "WARNING", tag="思维",
                )
                tool_chain.append({"role": "system", "content": feedback})
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 结束被拦截（存在未完成操作），已反馈 AI 修正"
                )
                state.iteration += 1
                return _StageOutcome.CONTINUE
        # end_reply 同批若带有 assistant 正文，按纯文本照常投递（与是否已 send_message 无关）
        if ctx.mode == ThinkMode.REPLY and ctx.anything:
            end_text = _strip_think_blocks(result.content or "").strip()
            if (
                end_text
                and not should_suppress(end_text)
                and not looks_like_fake_tool_call(end_text)
                and not looks_like_context_leak(end_text)
            ):
                target = target_from_anything(ctx.anything, ctx.adapter_key)
                if target is not None:
                    sent = await deliver_text(target, end_text)
                    if sent:
                        execution_steps.append(
                            f"→ 第{state.iteration + 1}轮: end_reply 附带纯文本已投递到 "
                            f"{target.session_key}"
                        )
                    else:
                        execution_steps.append(
                            f"→ 第{state.iteration + 1}轮: end_reply 附带纯文本投递失败"
                            f"（{target.session_key}）"
                        )
        log(f"AI 主动结束{ctx.mode_label} (轮次 {state.iteration + 1})", tag="思维")
        # Plan 收敛由 finish_think 统一处理（所有正常结束路径的必经之地）
        await _finish_round(ctx, state)
        return _StageOutcome.BREAK

    # 每轮工具批次结束：程序级自动推进 plan 步骤（兜底，REPLY/REFLECT 通用）。
    # 仅当本轮调用了**非 plan 管理工具**（实际干活的工具）才推进——
    # present_plan 当轮不推进（工作还没开始），update_goal 当轮不推进
    # （AI 已精确标记，无需兜底）。tracker 内部按 scope 过滤 + 无 active plan
    # 时快速返回，成本可忽略。
    if called - _PLAN_MANAGEMENT_TOOL_NAMES:
        try:
            from agent.planning import tracker as _plan_tracker
            await _plan_tracker.advance_plan_step(ctx.current_scope)
        except Exception:
            pass  # 自动推进失败不影响主流程

    state.iteration += 1
    return _StageOutcome.CONTINUE


# ==================================================================
# 工具执行
# ==================================================================

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
        blocked_tools: Optional[AbstractSet[str]] = None,
) -> None:
    """执行工具调用并将 assistant + tool 消息追加到 tool_chain。

    保留 content 和推理字段以维持多轮思维链连续性。
    实际发送内容由工具（如 send_message）的 _record_to_context 负责写入 DB。
    结果加工（脱敏/扫描/守卫/截断）由 ToolResultPipeline 统一处理。
    blocked_tools 为模式级禁用工具：执行侧拦截返回合成错误（可见性与权限分离）。
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
    preserve_reasoning_fields(assistant_msg, result, tool_turn=True)
    tool_chain.append(assistant_msg)

    # 守卫执行前检查：已知必败/无进展的调用直接返回合成结果，不执行真实工具
    # 模式级禁用工具（内部任务禁外发等）同样在此拦截：schema 保留在数组中
    # 保持前缀缓存一致，执行侧统一兜底（可见性与权限分离）
    blocked_results: Dict[str, str] = {}
    for tc in tool_calls:
        if blocked_tools and tc.name in blocked_tools:
            from core.tool_errors import ErrorCause, tool_error
            blocked_results[tc.id] = tool_error(
                f"工具 {tc.name} 在当前模式（内部任务/受限角色）下不可用",
                cause=ErrorCause.PERMISSION, retryable=False,
                hint="该工具仅被限制而非必需：请改用允许的工具完成任务，勿重复调用",
            )
            log(f"模式禁用工具拦截: {tc.name}", "DEBUG", tag="思维")
    if guardrail is not None:
        for tc in tool_calls:
            if tc.id in blocked_results:
                continue
            decision = guardrail.before_call(tc.name, tc.arguments or "")
            if decision.should_block:
                blocked_results[tc.id] = synthetic_block_result(decision)
                log(f"工具守卫拦截: {tc.name} ({decision.reason})", "WARNING", tag="思维")

    async def _run_one(tc: ToolCall) -> str:
        if tc.id in blocked_results:
            return blocked_results[tc.id]
        return await execute_one_tool(mind, tc, iteration, anything)

    # 并发安全分级（对齐 Claude Code）：连续只读调用并行（上限 10），写操作严格串行。
    # 无论哪条路径，tool 消息都按 tool_calls 原始顺序追加，保证配对完整。
    semaphore = asyncio.Semaphore(_MAX_TOOL_CONCURRENCY)

    async def _run_guarded(tc: ToolCall):
        async with semaphore:
            try:
                return await _run_one(tc)
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                return e

    for is_parallel, batch in _partition_tool_calls(tool_calls):
        if is_parallel and len(batch) > 1:
            outputs = await asyncio.gather(*[_run_guarded(tc) for tc in batch])
        else:
            outputs = [await _run_guarded(tc) for tc in batch]
        # 先按序追加全部 tool 结果，再统一注入多模态图片：
        # 逐条交错注入会在并行批次中形成 tool(A)→user(图)→tool(B)，
        # 破坏 Anthropic tool_result 邻接性导致会话级 400
        final_outputs: List[str] = []
        for tc, output in zip(batch, outputs, strict=False):
            if isinstance(output, BaseException):
                # 统一走归因映射（超时/网络/权限…），不裸抛 str(exc)
                output = error_from_exception(output, action=f"工具 {tc.name} 执行")
            output_str = output if isinstance(output, str) else str(output)
            try:
                final_output = pipeline.process(
                    tc.name, tc.arguments or "", output_str,
                    skip_guardrail=tc.id in blocked_results,
                )
            except Exception as e:
                # 配对铁律：结果加工失败也要保证 tool 消息落链
                final_output = error_from_exception(e, action=f"工具 {tc.name} 结果加工")
            tool_chain.append({"role": "tool", "tool_call_id": tc.id, "content": final_output})
            final_outputs.append(final_output)
        for final_output in final_outputs:
            # 多模态工具结果：候选图片注入上下文，让视觉模型直接看到（如表情包检索）
            try:
                await _append_multimodal_result(mind, tool_chain, final_output)
            except Exception as exc:
                log(f"多模态工具结果展开失败（不影响主流程）: {exc}", "DEBUG", tag="思维")
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
    tool_scope = getattr(anything, "entity_scope", "") if anything is not None else ""
    await event_bus.emit(EVENT_TOOL_EXECUTED, {"tool": tc.name, "iteration": iteration})
    await event_bus.emit(EVENT_THINKING_TOOL_START, {
        "scope": tool_scope,
        "tool_name": tc.name,
        "tool_id": tc.id,
        "arguments_preview": tc.arguments[:300] if tc.arguments else "",
        "iteration": iteration,
    })
    log(f"执行工具: {tc.name}", tag="思维")

    # 批准机制：在执行前检查是否需要人工批准
    denied = await _request_tool_approval(tc, anything, tool_scope)
    if denied is not None:
        return denied

    t0 = time.time()
    try:
        result = await mind.tool_executor(tc)  # type: ignore[misc]
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "scope": tool_scope,
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "result_preview": result[:300] if result else "",
            "success": True,
        })
        # 用户 hook（tool_post）：fire 型事件，阻塞语义对工具结果无意义，
        # 只投递预览（记录/通知类脚本的挂点）。空配置零开销短路
        from agent.hooks import hooks_active, run_event_hooks
        if hooks_active("tool_post"):
            try:
                await run_event_hooks(
                    "tool_post", tool_name=tc.name,
                    arguments=(tc.arguments or "")[:500],
                    scope=tool_scope,
                    result_preview=result[:400] if isinstance(result, str) else "",
                )
            except Exception:
                pass  # hook 失败不影响已产出的工具结果
        return result
    except Exception as exc:
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "scope": tool_scope,
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
        return error_from_exception(exc, action=f"工具 {tc.name} 执行")


def preserve_reasoning_fields(msg: Dict[str, Any], result: ChatResult,
                              tool_turn: bool = False) -> None:
    """从 ChatResult.raw 中提取推理字段到 assistant 消息，维持多轮思维链。

    litellm 统一返回 OpenAI 格式，按协议覆盖两种载体：
    - reasoning_details：OpenRouter 风格，litellm 请求侧原样回传。
      **仅 tool_turn=True（工具调用轮）挂载**——DeepSeek 官方 thinking 规则
      要求工具轮回传、纯文本轮服务端直接忽略，普通轮回传纯属 token 浪费
      （对齐 dsh serialize.ts 的条件回传；REFLECT 连续文本轮场景收益最大）
    - thinking_blocks：Anthropic 协议 thinking 块（含 signature/redacted），
      litellm 请求侧据此重构 thinking 块（交错思考 + tool_use 场景必需）。
      签名块语义微妙，保持无条件保留（不随本参数收紧，单独评估）
    均以响应实际存在为条件，不返回推理字段的模型行为不变。
    """
    if not result.raw or not result.reasoning_content:
        return
    try:
        choices = result.raw.get("choices")
        if not choices or not isinstance(choices, list):
            return
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            return
        if tool_turn:
            rd = message.get("reasoning_details")
            if rd:
                msg["reasoning_details"] = rd
        tb = message.get("thinking_blocks")
        if tb:
            msg["thinking_blocks"] = tb
    except (IndexError, AttributeError, TypeError):
        pass


def log_tool_round(iteration: int, tool_calls: List[ToolCall]) -> None:
    log(
        f"第 {iteration + 1} 轮工具调用: "
        f"{', '.join(tc.name for tc in tool_calls)}",
        tag="思维",
    )
