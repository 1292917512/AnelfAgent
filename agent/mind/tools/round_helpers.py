"""思维循环的内部支撑：回合状态、结果判定守卫与循环外支撑 helper。

从 think_loop 拆出的叶子模块（不依赖 think_loop / reply_finalize），
供 think_loop 主循环与阶段函数复用；函数以 mind 实例为第一参数。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from agent.mind.tools.result_pipeline import ToolResultPipeline
from agent.mind.tools.vision import apply_vision
from core.event_bus import EVENT_THINKING_TOOL_END, event_bus
from core.log import log
from core.stream_events import EVENT_ASSISTANT_DELTA, EVENT_CONTEXT_USAGE

if TYPE_CHECKING:
    from agent.llm import ChatResult, ToolCall
    from agent.messages import Everything
    from agent.mind.background_tasks import (
        BackgroundTaskInfo,
        BackgroundTaskRegistry,
        TaskCompletion,
    )
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind


class ThinkMode(str, Enum):
    """思维循环模式。"""

    REPLY = "reply"
    """对话模式：处理用户消息，通过工具发送回复。"""

    REFLECT = "reflect"
    """反思模式：内省思考，收集文本输出，不发送消息。"""


# ==================================================================
# 回合状态
# ==================================================================

class _StageOutcome(str, Enum):
    """阶段函数的处理结果，驱动主循环的后续动作。"""

    PROCEED = "proceed"
    """继续本轮的后续阶段。"""

    CONTINUE = "continue"
    """本轮已处理完，进入下一轮迭代。"""

    BREAK = "break"
    """结束整个思维循环。"""


@dataclass
class _ThinkRoundState:
    """think_loop 的回合可变状态（计数器/水位/预算，每轮更新）。"""

    iteration: int = 0
    consecutive_fake_calls: int = 0
    consecutive_empty_calls: int = 0
    consecutive_tool_errors: int = 0
    consecutive_security_leaks: int = 0
    consecutive_overflow_compressions: int = 0
    reflect_text_rounds: int = 0
    end_reply_interceptions: int = 0
    # 无工具正文守卫计数：有未完成 plan 时纯文本被拦截的次数（防死循环上限 2）
    plan_text_guard_count: int = 0
    # 前言守卫：承诺式过渡文本（"我来看看…"）被拦截的次数与暂存内容
    # （防"只说不做"，上限 2；被拦的前言在终态投递时合并进最终回复，不丢表达）
    preamble_guard_count: int = 0
    preamble_parts: List[str] = field(default_factory=list)
    max_output_recoveries: int = 0
    last_prompt_tokens: int = 0
    # 上一轮是否仅为输出类工具（send_message 等）且已成功发送：
    # 其后紧跟的纯文本不再代发，直接结束，避免重复出站
    prev_round_outbound_only: bool = False
    # 后台任务等待：本轮回复累计预算（秒）
    wait_budget: float = 0.0
    # 新消息并入基线水位（快照内最大 ts_ns）
    last_merged_ts: int = 0


@dataclass
class _ThinkLoopCtx:
    """think_loop 的会话级上下文：循环期间共享的服务与消息容器。

    base_messages / tool_chain / active_tools 会在压缩、工具集重建、
    新消息并入时被整体替换，故以可变字段持有。
    """

    mind: "Mind"
    mode: ThinkMode
    anything: Optional["Everything"]
    adapter_key: str
    options: Optional[Dict]
    base_messages: List[Dict]
    tool_chain: List[Dict]
    execution_steps: List[str]
    collected_text: List[str]
    active_tools: List[Dict]
    current_scope: str
    interrupts: Any
    background: Optional["BackgroundTaskRegistry"]
    wait_per_round: float
    pure_tool_mode: bool
    mode_label: str
    guardrail: "GuardrailController"
    pipeline: ToolResultPipeline
    turn_id: str
    delta_emitter: Callable[[str, bool], Awaitable[None]]
    supports_stream: bool
    # 会话内冻结的工具顺序（首轮排序快照）：版本变化重建工具集时按此顺序重排，
    # 新出现的工具按名称追加尾部——避免使用计数驱动的层间跳变击穿 provider 前缀缓存
    frozen_tool_order: Optional[List[str]] = None


# ==================================================================
# 会话初始化
# ==================================================================

def _make_delta_emitter(
        current_scope: str,
        turn_id: str,
) -> Callable[[str, bool], Awaitable[None]]:
    """构造流式过程事件发射器（增量事件供通道订阅，webui 流式渲染）。"""
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

    return delta_emitter


def _probe_stream_support(mind: "Mind") -> bool:
    """替身 Mind（测试/子代理）可能仍是非流式签名：探测后按需传参。"""
    try:
        import inspect as _inspect
        _invoke_params = _inspect.signature(mind._invoke_llm_unified).parameters
        return "stream" in _invoke_params or any(
            p.kind == _inspect.Parameter.VAR_KEYWORD for p in _invoke_params.values()
        )
    except (TypeError, ValueError):
        return False


def _tool_schema_name(schema: Dict) -> str:
    """从 OpenAI function schema 取工具名。"""
    return str(schema.get("function", {}).get("name", ""))


def _apply_frozen_tool_order(schemas: List[Dict], frozen_order: Optional[List[str]]) -> List[Dict]:
    """按会话首轮冻结的工具顺序重排重建后的工具集。

    已消失的工具跳过，新出现的工具按名称排序追加尾部——
    保持 tools 数组在会话内字节级稳定，避免击穿 provider 前缀缓存。
    """
    if not frozen_order:
        return schemas
    by_name = {_tool_schema_name(s): s for s in schemas}
    ordered = [by_name[name] for name in frozen_order if name in by_name]
    newcomers = sorted(
        (s for name, s in by_name.items() if name not in set(frozen_order)),
        key=_tool_schema_name,
    )
    return ordered + newcomers


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
) -> tuple[_ThinkLoopCtx, _ThinkRoundState]:
    """think_loop 会话初始化：adapter/基线消息/快照水位/守卫/管线/流式探测。"""
    from agent.mind.guardrails import GuardrailController
    from agent.mind.tool_activation import ToolActivationManager

    mode_label = "反思" if mode == ThinkMode.REFLECT else "对话"
    # adapter_key 优先使用调用方传入（按 scope 隔离），回退到共享状态（兼容旧路径）
    if not adapter_key and mode == ThinkMode.REPLY:
        adapter_key = mind._resolve_adapter_key()
    if base_messages is None:
        if mode == ThinkMode.REPLY and anything:
            base_messages = await mind.get_recollection(anything=anything)
        else:
            base_messages = []

    mc = mind._get_mind_config()
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

    # 中断注册表（协作式刹车信号；替身 Mind 可能不具备，容忍缺省）
    interrupts = getattr(mind, "interrupts", None)

    # 流式过程事件：turn_id 标识本轮思维会话
    turn_id = uuid.uuid4().hex[:8]

    ctx = _ThinkLoopCtx(
        mind=mind,
        mode=mode,
        anything=anything,
        adapter_key=adapter_key,
        options=options,
        base_messages=base_messages,
        tool_chain=tool_chain,
        execution_steps=execution_steps,
        collected_text=collected_text,
        active_tools=active_tools,
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
        supports_stream=_probe_stream_support(mind),
    )
    state = _ThinkRoundState(wait_budget=wait_budget, last_merged_ts=last_merged_ts)
    return ctx, state


# ==================================================================
# 循环支撑 helper
# ==================================================================

def _consume_pending_for_scope(mind: "Mind", anything: "Everything") -> None:
    """消费当前 scope 的待处理队列条目（新消息已并入当前循环，无需另起周期）。"""
    try:
        from agent.mind.prefrontal_cortex import _safe_entity_scope
        mind.pfc.consume_scope_task(_safe_entity_scope(anything))
    except Exception as exc:
        log(f"消费待处理队列失败: {exc}", "DEBUG", tag="思维")


async def _fetch_new_user_messages(
        mind: "Mind",
        anything: "Everything",
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
        mind: "Mind",
        base_messages: List[Dict],
        tool_chain: List[Dict],
        scope: str,
) -> tuple[List[Dict], List[Dict]]:
    """执行上下文压缩（保头保尾 + 中间摘要），返回新的 (base_messages, tool_chain)。

    压缩成败记录到熔断器（连续失败 3 次停止尝试）；
    成功后执行 rehydration：重读压缩前正在处理的文件，恢复工作现场
    （对齐 Claude Code post-compact rehydration，消费 file_state 缓存）。
    """
    try:
        new_base, new_chain = await mind.compressor.compress_messages(
            base_messages, tool_chain,
            scope=scope,
            summarizer=mind.summarize_text,
        )
    except Exception as exc:
        mind.compressor._record_compress_result(False)
        log(f"上下文压缩失败: {exc}", "WARNING", tag="压缩")
        raise
    mind.compressor._record_compress_result(True)
    rehydrated = await asyncio.to_thread(_rehydrate_recent_files, scope)
    if rehydrated:
        new_chain = [*new_chain, {"role": "system", "content": rehydrated}]
    return new_base, new_chain


# rehydration 单文件字符上限与总预算（对齐 Claude Code 5K/文件、50K 总量）
_REHYDRATE_MAX_FILES = 5
_REHYDRATE_PER_FILE_CHARS = 5000
_REHYDRATE_TOTAL_CHARS = 50000


def _rehydrate_recent_files(scope: str) -> str:
    """压缩后重读最近读取/编辑过的文件（≤5 个），生成恢复上下文。"""
    try:
        from entities.filesystem import file_state
        cache = file_state.get_cache(scope)
        entries = cache.recent_entries(_REHYDRATE_MAX_FILES)
    except Exception:
        return ""
    if not entries:
        return ""
    import os
    sections: List[str] = []
    total = 0
    for path, _state in entries:  # 最近使用的优先
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_REHYDRATE_PER_FILE_CHARS + 1)
        except OSError:
            continue
        if len(content) > _REHYDRATE_PER_FILE_CHARS:
            content = content[:_REHYDRATE_PER_FILE_CHARS] + "\n... (截断)"
        block = f"### {os.path.basename(path)} ({path})\n```\n{content}\n```"
        if total + len(block) > _REHYDRATE_TOTAL_CHARS:
            break
        sections.append(block)
        total += len(block)
    if not sections:
        return ""
    return (
        "[系统] 上下文已压缩。以下是你压缩前正在处理的文件的最新内容"
        "（自动恢复，供继续工作参考；如需编辑请遵循 read-before-write 流程）：\n"
        + "\n\n".join(sections)
    )


def _format_running_tasks(tasks: List["BackgroundTaskInfo"]) -> str:
    """运行中任务的一行式摘要（注入提示词用）。"""
    return "、".join(f"{t.description[:30]}({t.task_id})" for t in tasks) or "无"


def _format_task_completions(
        completions: List["TaskCompletion"],
        running: List["BackgroundTaskInfo"],
) -> str:
    """后台任务完成事件注入文本（system 角色，保持消息交替规范）。"""
    lines = ["[后台任务完成] 你等待的后台任务已结束："]
    for c in completions:
        status = "成功" if c.success else "失败"
        lines.append(f"- {c.description[:60]} ({c.task_id})：{status}")
        if c.summary:
            lines.append(f"  结果：{c.summary[:800]}")
    if running:
        lines.append(f"仍有 {len(running)} 个任务运行中：{_format_running_tasks(running)}")
    lines.append(
        "请根据结果继续处理："
        "任务未完成则继续调用工具；全部完成后再回复用户（send_message 或最终正文）；"
        "无需回复则 end_reply。"
    )
    return "\n".join(lines)


async def _suspend_for_background(
        mind: "Mind",
        anything: "Everything",
        registry: "BackgroundTaskRegistry",
        scope: str,
        since_ts: int,
        timeout: float,
        interrupts,
) -> tuple[str, List["TaskCompletion"], float]:
    """挂起等待后台任务完成，返回 (reason, completions, elapsed)。

    挂起期间新消息照常实时入库（accept_feel 不经思考循环）；
    should_abort 轮询中断信号与新消息水位，被打断时立即返回，
    由循环顶部统一并入新消息 / 处理中断，时序不受挂起影响。
    """
    log(f"检测到等待意图，挂起等待后台任务 (scope={scope}, 上限 {timeout:.0f}s)", tag="思维")

    async def _aborted() -> bool:
        if interrupts is not None and interrupts.is_requested(scope):
            return True
        return bool(await _fetch_new_user_messages(mind, anything, since_ts))

    t0 = time.monotonic()
    result = await registry.wait_any(scope, timeout=timeout, should_abort=_aborted)
    return result.reason, result.completions, time.monotonic() - t0


async def _merge_new_messages(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> None:
    """并入循环期间到达的新用户消息（含携带媒体），更新工具集与并入水位。"""
    mind = ctx.mind
    if not (ctx.mode == ThinkMode.REPLY and ctx.anything):
        return
    new_msgs = await _fetch_new_user_messages(mind, ctx.anything, state.last_merged_ts)
    if not new_msgs:
        return
    for m in new_msgs:
        ctx.tool_chain.append({"role": "user", "content": m["content"]})
    state.last_merged_ts = new_msgs[-1]["ts_ns"]
    # 消费掉对应的待处理队列条目，避免该消息之后另起独立周期
    _consume_pending_for_scope(mind, ctx.anything)
    # 新消息携带的媒体：激活对应媒体工具并重建工具集供后续轮次使用
    # （媒体标签已随内容并入上下文，待处理媒体不留存到后续周期）
    try:
        merged_images = mind.pfc.collect_images(scope=ctx.current_scope)
        merged_media = mind.pfc.collect_media(scope=ctx.current_scope)
    except TypeError:
        merged_images = mind.pfc.collect_images()
        merged_media = mind.pfc.collect_media()
    if merged_images:
        # 视觉模型直传图片 block；非视觉模型转存超大 base64 为文件路径
        ctx.tool_chain = await apply_vision(mind, ctx.tool_chain, merged_images)
    if merged_images or merged_media:
        mind.pfc.activate_media_tools(merged_images, merged_media)
        rebuilt = await mind.pfc.get_active_tool_schemas(
            ctx.adapter_key, scope=mind._resolve_entity_scope(ctx.anything),
        )
        ctx.active_tools = _apply_frozen_tool_order(rebuilt, ctx.frozen_tool_order)
    log(f"并入 {len(new_msgs)} 条循环期间新消息到当前上下文", tag="思维")
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮前: 并入 {len(new_msgs)} 条新消息")


async def _emit_context_usage(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> None:
    """上下文用量快照（usage 锚定：API 真实用量优先；供 webui 状态栏显示）。"""
    mind = ctx.mind
    if mind.compressor is None:
        return
    try:
        _tokens = state.last_prompt_tokens or mind.compressor.estimate_tokens(
            ctx.base_messages + ctx.tool_chain)
        _threshold = mind.compressor.threshold_tokens()
        _window = mind.get_model_context_length()
        if _threshold > 0:
            await event_bus.emit(EVENT_CONTEXT_USAGE, {
                "scope": ctx.current_scope,
                "tokens": _tokens,
                "threshold": _threshold,
                "window": _window,
                "percent": round(_tokens / _threshold * 100, 1),
            })
    except Exception:
        pass  # 状态事件失败不影响主流程


async def _handle_overflow(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        exc: Exception,
) -> bool:
    """上下文溢出压缩路径：LLM 上下文超限时紧急压缩后重试。

    已压缩（可安全重试）返回 True；非溢出错误或连续压缩无效返回 False，
    由主循环原样抛出（连续压缩无效时放弃，防止死循环）。
    """
    from agent.llm.resilience import classify_llm_error

    mind = ctx.mind
    classified = classify_llm_error(exc)
    if not (
        classified.should_compress
        and mind.compressor is not None
        and state.consecutive_overflow_compressions < 2
    ):
        return False
    state.consecutive_overflow_compressions += 1
    log(
        f"LLM 上下文超限，执行紧急压缩 (第 {state.consecutive_overflow_compressions} 次)",
        "WARNING", tag="压缩",
    )
    async with mind.compressor.scope_lock(ctx.current_scope):
        ctx.base_messages, ctx.tool_chain = await _compress_context(
            mind, ctx.base_messages, ctx.tool_chain, ctx.current_scope,
        )
    # 紧急压缩后旧真用量已失真，清零防止下轮误判再次溢出
    state.last_prompt_tokens = 0
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 上下文超限，已紧急压缩")
    state.iteration += 1
    return True


# ==================================================================
# 阶段函数（无 finish_think 依赖的叶子阶段）
# ==================================================================

# max_output_tokens 截断恢复（对齐 Claude Code，最多 3 次）
_MAX_OUTPUT_RECOVERY_LIMIT = 3
_PROMPT_MAX_OUTPUT_CONTINUE = (
    "[系统] 你的上一条输出达到了长度上限被截断。"
    "请直接从中断处继续，不要道歉、不要复述之前的内容；"
    "如果剩余工作较多，请拆分为更小的步骤逐步完成。"
)


def _handle_length_recovery(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: "ChatResult",
) -> _StageOutcome:
    """max_tokens 截断续写（对齐 Claude Code 两级恢复，最多 3 次）。

    截断轮的 tool_calls 参数可能不完整（JSON 断裂），一律丢弃不执行；
    恢复次数耗尽时同样跳过本轮 tool_calls 并提示拆分/结束。
    """
    # 延迟导入：preserve_reasoning_fields 定义在 think_loop（工具执行块），避免循环引用
    from agent.mind.tools.think_loop import preserve_reasoning_fields

    if getattr(result, "finish_reason", "") != "length":
        return _StageOutcome.PROCEED

    if state.max_output_recoveries < _MAX_OUTPUT_RECOVERY_LIMIT:
        state.max_output_recoveries += 1
        log(f"输出被 max_tokens 截断，注入续写提示 (第 {state.max_output_recoveries} 次)",
            "WARNING", tag="思维")
        partial_text = _strip_think_blocks(result.content or "").strip()
        if partial_text or result.tool_calls:
            truncated_msg: Dict[str, Any] = {"role": "assistant", "content": partial_text}
            preserve_reasoning_fields(truncated_msg, result)
            ctx.tool_chain.append(truncated_msg)
        ctx.tool_chain.append({"role": "system", "content": _PROMPT_MAX_OUTPUT_CONTINUE})
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 输出截断，已注入续写提示")
        state.iteration += 1
        return _StageOutcome.CONTINUE

    # 恢复次数耗尽：跳过本轮 tool_calls（参数可能不完整，执行会出错）
    log("输出截断恢复次数耗尽，跳过本轮 tool_calls 并结束", "WARNING", tag="思维")
    partial_text = _strip_think_blocks(result.content or "").strip()
    if partial_text:
        truncated_msg = {"role": "assistant", "content": partial_text}
        preserve_reasoning_fields(truncated_msg, result)
        ctx.tool_chain.append(truncated_msg)
    ctx.tool_chain.append({
        "role": "system",
        "content": "[系统] 输出多次被截断，本轮工具调用参数可能不完整，已跳过执行。"
                   "请拆分为更小的步骤或调用 end_reply 结束。",
    })
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 截断恢复耗尽，跳过 tool_calls")
    state.iteration += 1
    return _StageOutcome.CONTINUE


# ==================================================================
# 回合结果判定守卫
# ==================================================================

_END_REPLY_TOOL_NAME = "end_reply"

# 查资料等非输出工具后：结果仅自己可见；未完成则继续调工具，完成后再回用户
_OUTPUT_TOOL_NAMES = frozenset({
    "send_message", "send_photo", "send_voice", "send_file",
})


def should_end_reply(tool_calls: List["ToolCall"]) -> bool:
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


def _parse_tool_result_json(text: str) -> Optional[Any]:
    """宽松解析工具结果 JSON。

    结果经加工管线后可能带威胁扫描前缀（[安全警告] ...\n）或
    守卫警告后缀（\n\n[工具守卫警告: ...]），整体 json.loads 会失败；
    此处定位首个 '{' 起解析首个完整 JSON 值，容忍前后附加文本。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


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
        parsed = _parse_tool_result_json(r)
        if not isinstance(parsed, dict):
            # 非 JSON dict 内容视为非错误（纯文本结果）
            return False
        # 有 error 键 → 错误，继续检查下一个
        if "error" in parsed:
            continue
        # success=false / ok=false → 错误，继续检查下一个
        if parsed.get("success") is False or parsed.get("ok") is False:
            continue
        # 无错误信号，至少一个成功
        return False
    # 全部都是错误结果
    return True


def _extract_error_text(payload: Any) -> str:
    """从工具结果 payload（dict 或 JSON 字符串）中提取错误文本，无错误返回空串。"""
    if isinstance(payload, str):
        payload = _parse_tool_result_json(payload)
    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False or payload.get("ok") is False:
        return str(payload.get("error", "") or "未知错误")
    if payload.get("error"):
        return str(payload["error"])
    return ""


_PROMPT_END_BLOCKED_FAILURE = (
    "[系统拦截] 结束请求未生效：本轮以下工具执行失败，相关操作未完成：\n"
    "{failures}\n"
    "请根据错误原因修正后重新调用失败的工具"
    "（注意：target_id 等 ID 类参数必须按 schema 声明传字符串类型，不要传数字），"
    "全部成功后再调用 end_reply 结束。若确认无法修复，可再次调用 end_reply 强制结束。"
)


def _collect_round_failures(tool_chain: List[Dict], tool_calls: List["ToolCall"]) -> str:
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
        parsed = _parse_tool_result_json(content)
        if not isinstance(parsed, dict):
            continue

        err = _extract_error_text(parsed)
        if err:
            failures.append(f"{tc_names.get(tc_id, '?')}: {err}")

    if not failures:
        return ""
    lines = "\n".join(f"- {f}" for f in failures)
    return _PROMPT_END_BLOCKED_FAILURE.format(failures=lines)


# ------------------------------------------------------------------
# Plan 程序级自动进度（不依赖 AI 调 update_goal）
#
# 设计原则（参考 hermes 的 progress callback / Claude Code 的
# updateProgressFromMessage）：进度由程序从执行流自动推断，AI 不需要
# 主动汇报；AI 调 update_goal 只是"可选的精确标记"，不是必要条件。
#
# 全部状态机与事件发射统一由 ``agent.planning.tracker`` 实现：
# - present_plan 工具 → tracker.submit_plan（公告 + 首步 in_progress）
# - 每轮工具批次后 → tracker.advance_plan_step（粗粒度兜底）
# - finish_think → tracker.finalize_plan（收敛终态，诚实语义）
# - 无工具正文终态前 → tracker.guard_feedback_for_text_only（守卫）
# - cancel-plan 路由 → tracker.cancel_plan
# ------------------------------------------------------------------

# 计划管理工具：调用它们不算"执行了一步"，不触发自动推进。
# 否则 present_plan 当轮 step 0 就被误标完成（进度超前 bug）。
_PLAN_MANAGEMENT_TOOL_NAMES = frozenset({
    "present_plan", "update_goal", "create_goal", "list_goals", "get_goal", "delete_goal",
})


def _round_output_sent_successfully(
        tool_chain: List[Dict], tool_calls: List["ToolCall"],
) -> bool:
    """本轮是否已通过输出类工具成功发送（send_message / 媒体发送）。"""
    tc_ids = {
        tc.id for tc in tool_calls if tc.name in _OUTPUT_TOOL_NAMES
    }
    if not tc_ids:
        return False
    found_tool = False
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            # 尾部多模态注入的 user 消息跳过；遇到轮次边界（已见 tool 消息）停止
            if found_tool:
                break
            continue
        found_tool = True
        if msg.get("tool_call_id") not in tc_ids:
            continue
        parsed = _parse_tool_result_json(msg.get("content", ""))
        if isinstance(parsed, dict) and parsed.get("success") is not False:
            return True
    return False


def _streaming_enabled() -> bool:
    """流式内核开关（配置 mind_streaming_enabled，默认开）。

    流式只产生过程事件（assistant_delta），回复出口仍是
    send_message/end_reply —— 多频道语义不受影响。
    """
    try:
        from core.config import get_config_bool
        return get_config_bool("mind_streaming_enabled", True)
    except Exception:
        return True


# ==================================================================
# 工具执行支撑
# ==================================================================

# 并行执行上限（对齐 Claude Code CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY 默认 10）
_MAX_TOOL_CONCURRENCY = 10


def _partition_tool_calls(tool_calls: List["ToolCall"]) -> List[tuple]:
    """按并发安全性把工具调用切分为连续批次（对齐 Claude Code toolOrchestration）。

    连续的并发安全调用组成并行批次，其余各自串行。
    安全判定 fail-closed：查询失败一律视为不安全。
    """
    from core.entity import EntityRegistry

    def _is_safe(tc: "ToolCall") -> bool:
        try:
            entity = EntityRegistry.get(tc.name)
            return bool(entity and entity.meta.get("concurrency_safe"))
        except Exception:
            return False

    partitions: List[tuple] = []
    for tc in tool_calls:
        safe = _is_safe(tc)
        if safe and partitions and partitions[-1][0]:
            partitions[-1][1].append(tc)
        else:
            partitions.append((safe, [tc]))
    return partitions


async def _request_tool_approval(
        tc: "ToolCall",
        anything: Optional["Everything"],
        tool_scope: str,
) -> Optional[str]:
    """批准机制：在执行前检查是否需要人工批准。

    未获批准时返回合成错误 JSON（并关闭链路中的工具节点）；
    批准、无 anything 或批准模块缺失时返回 None，调用方继续真实执行。
    """
    if anything is None:
        return None
    try:
        from agent.approval import ApprovalDecision, get_approval_gate

        gate = get_approval_gate()
        # 从 anything 提取上下文
        adapter_key = getattr(anything, "adapter_key", "") or "unknown"
        user_id = str(getattr(anything, "uid", "") or getattr(anything, "user_id", "") or "unknown")
        group_id = str(getattr(anything, "group_id", "") or "")
        chat_id = group_id if group_id not in ("", "0") else user_id

        # 获取频道实例
        from agent.channel.manager import get_channel_manager
        channel = get_channel_manager().get(adapter_key)

        if channel:
            # 解析工具参数
            try:
                tool_args = json.loads(tc.arguments) if tc.arguments else {}
            except (json.JSONDecodeError, TypeError):
                tool_args = {"_raw": tc.arguments or ""}

            decision = await gate.request_approval(
                tool_name=tc.name,
                tool_args=tool_args,
                reason=f"AI 请求调用工具 {tc.name}",
                channel=channel,
                chat_id=chat_id,
                user_id=user_id,
            )
            if decision != ApprovalDecision.APPROVED:
                log(
                    f"工具 {tc.name} 未获批准: {decision.value}",
                    "WARNING",
                    tag="批准",
                )
                # 关闭链路中的工具节点，避免一直停留在执行中
                await event_bus.emit(EVENT_THINKING_TOOL_END, {
                    "scope": tool_scope,
                    "tool_name": tc.name,
                    "tool_id": tc.id,
                    "duration_ms": 0,
                    "error": f"未获批准: {decision.value}",
                    "success": False,
                })
                return json.dumps({
                    "error": f"工具调用未获批准: {decision.value}。"
                             "用户已通过频道收到拒绝原因；请勿重试相同的调用，"
                             "可向用户说明情况或改用其他方式完成任务。",
                    "approval_decision": decision.value,
                }, ensure_ascii=False)
    except ImportError:
        # approval 模块未安装，跳过
        pass
    except Exception as exc:
        log(f"批准机制异常（继续执行）: {exc}", "WARNING", tag="批准")
    return None
