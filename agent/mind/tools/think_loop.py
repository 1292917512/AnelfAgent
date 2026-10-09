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
import json
import re
import time
from typing import TYPE_CHECKING, AbstractSet, Any, Dict, List, Optional, Set

from agent.channel.reply_route import (
    deliver_text,
    looks_like_context_leak,
    looks_like_fake_tool_call,
    looks_like_tool_call_text,
    should_suppress,
    target_from_anything,
)
from agent.llm import LLMCallAborted
from agent.mind.message_schema import preserve_reasoning_fields
from agent.mind.think_session import think_session
from agent.mind.tools.reply_finalize import complete_reply, finish_think
from agent.mind.tools.result_pipeline import (
    ToolResultPipeline,
)
from agent.mind.tools.round_helpers import (
    _END_REPLY_TOOL_NAME,
    _MAX_TOOL_CONCURRENCY,
    _OUTPUT_TOOL_NAMES,
    ThinkMode,
    _cache_status_hint,
    _check_tool_results_all_errors,
    _collect_round_error_briefs,
    _collect_round_failures,
    _compress_context,
    _consume_pending_for_scope,
    _detect_token_leak,
    _emit_context_usage,
    _format_running_tasks,
    _format_task_completions,
    _handle_length_recovery,
    _handle_overflow,
    _merge_new_messages,
    _merge_pushes,
    _merge_steered_messages,
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
    _token_budget_hint,
    merge_after_messages,
    resolve_tool_calls,
    should_end_reply,
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
from core.async_helper import suppress_task
from core.event_bus import (
    EVENT_BEFORE_REPLY,
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
    from agent.llm import ChatResult, ImageContent, ToolCall
    from agent.messages import Everything
    from agent.mind.background_tasks import BackgroundTaskInfo
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind
    from agent.mind.tools.reply_journal import ReplyToolJournal

# ==================================================================
# 上下文提供者实时注入（每轮尾部收集，零 I/O 契约 + 并发超时兜底）
# ==================================================================

async def _collect_provider_messages(mind: "Mind", scope: str) -> List[Dict]:
    """收集上下文提供者的当前最新快照，封装为注入消息列表。

    注入位置在工具链之后、exec_context 之前（每轮组装点调用）：
    时间/天气等实时内容逐轮新鲜，其字节变化又不打断工具链前缀缓存。
    收集失败 fail-open 为空列表（绝不影响主回复流程）。

    多模态契约（按 ContextMedia.kind 分派，物理约束见该类 docstring）：
    - clip 文本走 system 消息（与历史一致）；
    - image：视觉模型下集中组一条 user 角色多模态消息（image block 仅在
      user 角色可靠生效）；非视觉模型降级为 [media_type:image] 标签；
    - audio / video：一律降级为 [media_type:xxx][media_path:...] 标签
      并入所属 clip 的 system 文本（对话协议层不接受这两类 block；
      stable 层媒体规则已教会 AI 用对应工具处理标签）——不静默丢媒体。
    """
    try:
        from core.context_provider import ContextProviderRegistry
        clips, _metrics = await ContextProviderRegistry.collect(scope)
        config = getattr(getattr(mind, "llm", None), "config", None)
        vision = bool(config is not None and getattr(config, "supports_vision", False))

        messages: List[Dict] = []
        images: List["ImageContent"] = []
        for clip in clips:
            ref_lines: List[str] = []
            for m in clip.media:
                if m.kind == "image" and vision:
                    from agent.llm.types import ImageContent
                    images.append(ImageContent(
                        data=m.data, mime_type=m.mime_type or "image/jpeg",
                        is_url=m.is_url,
                    ))
                else:
                    # 降级路径：媒体以标签引用注入（与对话历史的媒体标签同构）
                    ref_lines.append(f"[media_type:{m.kind}][media_path:{m.data}]")
            text = clip.text
            if ref_lines:
                refs = " ".join(ref_lines)
                text = f"{text}\n{refs}" if text else f"[环境媒体] {refs}"
            if text:
                messages.append({
                    "role": "system",
                    "content": text,
                    "_layer": "provider",
                    "_source": {"origin": "context_provider"},
                })
        if images and config is not None:
            from agent.mind.tools.vision import build_provider_media_message
            media_msg = await build_provider_media_message(images, config)
            if media_msg is not None:
                messages.append(media_msg)
            else:
                # 全部加载/下载失败：降级为标签引用，不静默丢图
                refs = " ".join(
                    f"[media_type:image][media_path:{i.data}]" for i in images
                )
                messages.append({
                    "role": "system",
                    "content": f"[环境媒体] {refs}（画面加载失败，未能直注）",
                    "_layer": "provider",
                    "_source": {"origin": "context_provider"},
                })
        return messages
    except Exception as exc:
        log(f"上下文提供者收集失败: {exc}", "DEBUG", tag="思维")
        return []


# ==================================================================
# 回复入口（多轮对话循环装配 + 异常兜底）
# ==================================================================

async def reply_entry(
        mind: "Mind",
        anything: "Everything",
        images: Optional[List["ImageContent"]] = None,
        *,
        adapter_key: str = "",
        completion: Optional[Dict[str, Any]] = None,
) -> None:
    """执行回复，异常时发送错误提示。

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
        await _send_reply_error(anything, error_msg)
        await complete_reply(mind, anything, error_msg, 0, error=True,
                             completion=completion)


async def _send_reply_error(anything: "Everything", error_msg: str) -> None:
    """reply 异常时主动把错误提示发送到来源频道（避免用户端无反馈地空等）。"""
    try:
        target = target_from_anything(anything)
        if target is not None:
            await deliver_text(target, error_msg)
    except Exception as exc:
        log(f"错误提示发送失败: {exc}", "DEBUG", tag="思维")


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
    # adapter_key 优先使用调用方传入（按 scope 隔离），回退到共享状态（兼容旧路径）
    if not adapter_key:
        adapter_key = mind._resolve_adapter_key()
    scope = mind._resolve_entity_scope(anything) if anything else ""
    from core.tool_context import tool_request

    with think_session(mind, scope), tool_request(
        scope, str(getattr(anything, "uid", "") or ""),
        message_id=str(getattr(anything, "_trace_message_id", "") or getattr(anything, "adapter_message_id", "") or ""),
    ):
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
            options={"push_watermark": push_watermark} if push_watermark else None,
            adapter_key=adapter_key,
            completion=completion,
        )


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

_PROMPT_CONTINUE = (
    "[系统提示] 继续执行，若已完成所有操作请调用 end_reply 结束。"
)

# 输出契约（每轮注入）：回复一律走 send_message；纯文本保底为被动机制，不做提示。
_PROMPT_REPLY_GUIDE = (
    "[输出契约]\n"
    "1. 先做事再说话：需要查资料或执行操作时，立刻通过 function calling 调用工具；"
    "可并行的独立工具同一轮一并发起。\n"
    "2. 回复用户一律调用 send_message：中途进度、阶段性结论随时可发，"
    "不结束本轮，发完继续干活，不必等全部完成。\n"
    "3. 完成/无需回复 → 调用 end_reply（参数留空）静默收束："
    "同批正文不会投递给用户，回复必须经 send_message 发出。"
)

# 查资料等非输出工具后：结果仅自己可见；未完成则继续调工具，完成后再回用户
_PROMPT_AFTER_NON_OUTPUT_TOOLS = (
    "[系统提示] 工具结果仅你可见，不会自动发给用户。\n"
    "若任务尚未完成 → 继续调用工具，不要输出过程话术。\n"
    "若已全部完成 → 调用 send_message 发送最终回复；"
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

# 文本形态的 end_reply（弱模型把结束指令写成文本）：整条匹配即按结束意图处理
_END_REPLY_TEXT_RE = re.compile(r"^end_reply\s*\(.*\)\s*$", re.DOTALL)
# 文本形态 end_reply 转规范入链时使用的合成 tool_call id
_SYNTHETIC_END_REPLY_CALL_ID = "call_end_reply_text_intent"

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
        completion: Optional[Dict] = None,
        reflect_tool_selectors: Optional[List[str]] = None,
        require_output: bool = False,
) -> None:
    """统一思维循环：对话和反思共享同一流程。

    通过 mode 参数区分行为：
    - REPLY：处理用户消息，通过工具发送回复，写入对话历史
    - REFLECT：内省思考，收集文本输出到 collected_text，不发送消息

    base_messages 仅首轮获取，后续轮次复用缓存。
    工具集由调用方构建并传入，确保模式差异在入口处理。
    blocked_tools 为模式级禁用工具：schema 保留在数组中（跨调用前缀一致），
    执行侧拦截返回合成错误结果（可见性与权限分离）。
    reflect_tool_selectors 仅 REFLECT 模式使用：工具集版本变化重建
    active_tools 时按同一批选择器还原精简目录（与 mind.reflect 初始装配一致）。
    主循环只保留：中断检查 → LLM 调用 → 分发到阶段函数 → 终止条件判断。
    """
    ctx, state = await _prepare_think_context(
        mind, mode, tool_chain, execution_steps,
        collected_text, active_tools, anything, base_messages,
        options, adapter_key, blocked_tools, completion,
        reflect_tool_selectors=reflect_tool_selectors,
        require_output=require_output,
    )

    # 工具数组顺序由 ToolAssembly 跨回复追加式冻结（见 tool_assembly），
    # 会话内/回复间均字节稳定，无需在此再冻结。

    try:
        await _run_think_rounds(ctx, state, safety_limit, start_time)
    finally:
        # 结束原因收口（调用方传入 completion 容器时写出）：
        # 中断 > 预算耗尽标记 > 默认完成
        if ctx.completion is not None:
            ctx.completion["reason"] = (
                "interrupted" if state.interrupted else state.completion_reason
            )
            # 最终消息链（base + 工具链）随容器带出：SubAgent 据此持久化
            # transcript 供 follow_up 续跑。中断路径消息不完整，同样带出
            # （是否可续跑由 messages 是否存在决定，收束路径恒有值）。
            ctx.completion["messages"] = ctx.base_messages + ctx.tool_chain
        # 正常结束由 _finish_round 收敛；未走正常出口的中断、异常和预算耗尽只可取消。
        if not state.plan_finalized:
            try:
                from agent.planning import tracker as _plan_tracker
                await _plan_tracker.finalize_plan(
                    ctx.current_scope,
                    "cancelled",
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

        # 工具集版本检查：激活/发现/注册表变化时重建 active_tools。
        # REPLY 与无选择器的 REFLECT（reflect_share_reply_tools，与
        # mind.reflect 初始装配同族）走回复级全量装配（追加式冻结保证
        # 前缀字节稳定）；带选择器的 REFLECT 走精简目录装配（与初始
        # 装配同一入口，重建不会退回全量、也不会丢失选择器工具）
        cur_tools_version = (
            getattr(mind.pfc, "tools_version", 0),
            _tool_act_mgr.version,
            EntityRegistry.version(),
        )
        if cur_tools_version != last_tools_version:
            last_tools_version = cur_tools_version
            if mode is ThinkMode.REFLECT and ctx.reflect_tool_selectors:
                ctx.active_tools = await mind.pfc.get_reflect_tool_schemas(
                    ctx.adapter_key, scope=ctx.current_scope,
                    selectors=list(ctx.reflect_tool_selectors),
                )
            else:
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
            last_input_tokens=state.last_input_tokens,
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
                state.last_input_tokens = 0
                execution_steps.append(f"→ 第{state.iteration + 1}轮前: 上下文已压缩")

        # 并入循环期间到达的新用户消息（让 AI 在当前回复中一并处理，
        # 而非另起周期导致上下文断裂/忘记已回复）
        await _merge_new_messages(ctx, state)
        # 并入循环期间到达的实体推送（[push:] 系统通知，轮内弹窗）
        await _merge_pushes(ctx, state)
        # 并入转向指令（子代理步骤边界；主会话未绑定 drain 时零开销）
        _merge_steered_messages(ctx, state)

        exec_context = mind.pfc.build_execution_context(
            execution_steps, start_time, state.iteration,
            adapter_key=ctx.adapter_key, safety_limit=safety_limit,
            anything=anything,
            budget_hint=_token_budget_hint(ctx, state),
            cache_hint=_cache_status_hint(state),
        )
        # 纯工具模式（可选）且有可用工具时，API 级强制工具选择
        require_tools = bool(ctx.active_tools) and ctx.pure_tool_mode and not ctx.summary_only
        # 输出方式说明随执行上下文每轮注入
        exec_context["content"] += "\n" + (
            _PROMPT_REPLY_GUIDE if mode == ThinkMode.REPLY
            else _PROMPT_REFLECT_OUTPUT_DISCIPLINE
        )
        # exec_context（每轮动态）置于末尾：保持 stable/context/volatile/历史前缀
        # 字节稳定供 Prompt Caching 复用，且当前轮状态在模型注意力最强的末尾位置
        # （缓存断点不在此注入——发送边界由 llm/prompt_cache 按 _layer 统一装饰，
        # 链尾锚点天然随链增长前移）
        # provider 实时注入：每轮收集最新快照，置于工具链之后、exec_context 之前
        provider_msgs = await _collect_provider_messages(mind, ctx.current_scope)
        llm_messages = (
            ctx.base_messages + ctx.tool_chain + provider_msgs + [exec_context]
        )

        mind._set_phase(MindPhase.LLM_CALLING)
        # 流中早执行：只读调用在流继续生成时提前分发，工具轮按 id 回收
        early_runner = _EarlyToolRunner(
            mind, anything, state.iteration,
            blocked_tools=ctx.blocked_tools, guardrail=ctx.guardrail,
        )
        if ctx.summary_only:
            early_runner.abandon()
        # 超时/上下文超限已在 _invoke_llm_round 内注入恢复提示或紧急压缩
        result = await _invoke_llm_round(
            ctx, state, llm_messages, require_tools, early_runner=early_runner,
        )
        early_runner.abandon()
        if result is None:
            continue

        state.consecutive_overflow_compressions = 0
        if result.usage:
            # 输入占用锚点用归一化总输入口径：独占口径（原生 Anthropic）下
            # prompt_tokens 不含缓存读/写，以其为锚会低估真实占用
            if result.usage.total_input_tokens:
                state.last_input_tokens = result.usage.total_input_tokens
            state.last_cache_read_tokens = result.usage.cache_read_input_tokens
            state.last_cache_creation_tokens = result.usage.cache_creation_input_tokens
            state.last_cache_hit_rate = result.usage.cache_hit_rate
            state.last_cache_observable = result.usage.cache_observable

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

        if ctx.summary_only:
            outcome = await _handle_summary_round(ctx, state, result, tool_calls)
        elif not tool_calls:
            outcome = await _handle_text_only_round(ctx, state, result)
        else:
            outcome = await _handle_tool_round(
                ctx, state, result, tool_calls, early_runner=early_runner,
            )
        if outcome is _StageOutcome.BREAK:
            return

    # 达到安全上限：产出可能只是中途状态，标记结束原因供调用方决策
    state.completion_reason = "budget_exhausted"
    if ctx.completion is not None:
        ctx.completion["reason"] = state.completion_reason
    log(f"达到安全上限 ({safety_limit} 轮)，强制结束", "WARNING", tag="思维")
    if mode == ThinkMode.REPLY and anything:
        state.pending_text = ""
        target = target_from_anything(anything, ctx.adapter_key)
        if target is not None:
            await deliver_text(target, "本轮处理已达到执行上限，尚未确认整个任务完成。已启动的后台任务仍以实际状态为准。")
        await finish_think(mind, anything, execution_steps, safety_limit, ctx.tool_chain,
                           completion=ctx.completion, turn_id=ctx.turn_id)


# ==================================================================
# 阶段函数
# ==================================================================

async def _handle_summary_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
) -> _StageOutcome:
    """补交最终总结：本轮全部工具只返回拒绝结果，正文收集后立即收束。"""
    if tool_calls:
        await execute_tool_calls(
            ctx.mind, ctx.tool_chain, result, tool_calls, state.iteration, ctx.anything,
            guardrail=ctx.guardrail, pipeline=ctx.pipeline,
            blocked_tools=ctx.blocked_tools | {tc.name for tc in tool_calls},
            abort_event=ctx.abort_event,
        )
    else:
        _append_assistant_msg(ctx.tool_chain, result, result.content or "")
    text = _strip_think_blocks(result.content or "").strip()
    if text:
        ctx.collected_text.append(text)
    if merge_after_messages(ctx, state):
        state.iteration += 1
        return _StageOutcome.CONTINUE
    await _finish_round(ctx, state)
    return _StageOutcome.BREAK


async def _handle_interrupt(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> bool:
    """循环顶部的协作式中断检查；已中断则安全收束并返回 True。

    不发半截消息、不写残缺工具链、历史留中断元消息；在批内被中止的
    工具以占位结果保住配对，元消息提示"可能已部分执行"。
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
        aborted_hint = (
            "被中止的操作可能已部分执行，继续前请先核对实际状态"
            if state.partial_tool_abort
            else "未完成的操作已放弃"
        )
        await ctx.mind._add_system_context(
            ctx.anything,
            f"[系统] 本次回复在执行中被中断（{reason}），"
            f"{aborted_hint}，如需继续请重新发起。",
            role="system",
        )
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration, ctx.tool_chain,
            completion=ctx.completion, turn_id=ctx.turn_id,
        )
    return True


async def _invoke_llm_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        llm_messages: List[Dict],
        require_tools: bool,
        early_runner: Optional["_EarlyToolRunner"] = None,
) -> Optional[ChatResult]:
    """单次 LLM 调用；超时/上下文超限已处理（注入提示或紧急压缩）时返回 None。

    在途调用与 scope 中断事件竞争——用户刹车即刻生效（流式关闭流、
    非流式取消请求）；LLMCallAborted 返回 None，循环顶部的中断检查统一收束。
    """
    mind = ctx.mind
    on_tool_call_ready = early_runner.submit if early_runner is not None else None
    try:
        return await mind._invoke_llm_unified(
            llm_messages, ctx.active_tools or None, ctx.anything,
            tool_choice="required" if require_tools else None,
            options=ctx.options,
            purpose=ctx.mode.value,
            stream=_streaming_enabled(),
            on_delta=ctx.delta_emitter,
            on_tool_call_ready=on_tool_call_ready,
            abort_event=ctx.abort_event,
        )
    except LLMCallAborted:
        if early_runner is not None:
            early_runner.abandon(cancel=True)
        log(f"LLM 调用被用户中止 (轮次 {state.iteration + 1})，循环顶部收束", tag="中断")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: LLM 调用被用户中止")
        return None
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


async def _deliver_pending_text(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> None:
    """轮末统一投递点：把未经输出工具送达的最后一段独白保底投递给用户。

    纯文本在循环内只是独白（不终局、不中途投递）；强制收尾（独白掐断/守卫
    中止/安全上限等）时若仍有未送达文本，过滤沉默标记/伪造工具调用/上下文
    复述后投递一次。end_reply/[SILENT] 是静默收束——同批正文与暂存独白在
    各自收束分支直接丢弃，不经此投递。本轮已通过输出工具成功送达过则不再
    投递——收尾独白不外发，用户只收到 send_message 的内容。
    """
    text = state.pending_text
    state.pending_text = ""
    if ctx.mode != ThinkMode.REPLY or ctx.anything is None or not text:
        return
    if state.output_sent:
        log("本轮已有消息送达，未送达文本不再重复投递", "DEBUG", tag="思维")
        ctx.execution_steps.append(
            f"→ 第{state.iteration + 1}轮: 本轮已有消息送达，未送达文本不再重复投递"
        )
        return
    if should_suppress(text):
        return
    suppressed_kind = ""
    if looks_like_context_leak(text):
        suppressed_kind = "注入上下文复述"
    elif looks_like_fake_tool_call(text) or looks_like_tool_call_text(text):
        suppressed_kind = "工具调用形态文本"
    if suppressed_kind:
        # 病态输出不投递；发射观测事件供思维面板标红对应 LLM 节点
        log(f"轮末投递已过滤{suppressed_kind}", "WARNING", tag="思维")
        await event_bus.emit(
            EVENT_THINKING_FAKE_TOOL_CALL, {
                "iteration": state.iteration + 1,
                "consecutive": 1,
                "content_preview": text[:200],
            },
        )
        return
    target = target_from_anything(ctx.anything, ctx.adapter_key)
    if target is None:
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 未送达文本无投递目标，丢弃")
        return
    sent = await deliver_text(target, text)
    ctx.execution_steps.append(
        f"→ 第{state.iteration + 1}轮: 未送达文本已投递到 {target.session_key}"
        if sent
        else f"→ 第{state.iteration + 1}轮: 未送达文本投递失败（{target.session_key}）"
    )


async def _finish_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        *,
        deliver_pending: bool = True,
    allow_receipt: bool = True,
) -> None:
    """正常结束的统一收尾：轮末投递 + plan 收敛（全模式）+ REPLY 摘要入库/完成事件。

    - 轮末投递：暂存独白经 _deliver_pending_text 保底投递一次；静默收束
      （end_reply/[SILENT]）与安全泄露强制结束传 deliver_pending=False
      （收束即终局不投递 / 泄露文本绝不外发）。
    - plan 收敛：REPLY / REFLECT 正常结束都执行，scope 取自 ContextVar
    （``ctx.current_scope``），tracker 只处理当前 scope 的 active plan，无 plan 零成本。
    收敛成功后置位 ``state.plan_finalized``，think_loop 的 finally 不再重复收敛。
    - finish_think：仅 REPLY（摘要入库 + complete_reply 需要 anything）。
    异常路径（中断/安全上限）不走这里，由 think_loop 的 finally 统一收敛。
    """
    if allow_receipt:
        await _deliver_channel_receipt(ctx, state)
    if deliver_pending:
        await _deliver_pending_text(ctx, state)
    try:
        from agent.planning import tracker as _plan_tracker
        await _plan_tracker.finalize_plan(ctx.current_scope)
        state.plan_finalized = True
    except Exception:
        pass  # 收敛失败不影响主流程，finally 兜底重试
    if ctx.mode == ThinkMode.REPLY and ctx.anything:
        # 正常结束的唯一收口点：消息链已是终态，先写入 completion 再发完成事件
        # （complete_reply 读取该字段组装 EVENT_AFTER_REPLY 的 messages 快照；
        # think_loop finally 的写入面向 SubAgent 续跑场景，时机晚于本路径）
        if ctx.completion is not None:
            ctx.completion["messages"] = ctx.base_messages + ctx.tool_chain
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration + 1, ctx.tool_chain,
            completion=ctx.completion, turn_id=ctx.turn_id,
        )


async def _deliver_channel_receipt(ctx: _ThinkLoopCtx, state: _ThinkRoundState) -> None:
    """频道可声明只依赖工具事实的收尾回执，已送达或中断的回复不补发。"""
    if ctx.mode != ThinkMode.REPLY or ctx.anything is None or state.output_sent or state.interrupted:
        return
    from agent.channel.reply_policy import ReplyToolResult, get_reply_policy
    from agent.mind.tools.result_parse import parse_tool_result_json

    formatter = get_reply_policy(ctx.adapter_key).result_receipt
    if formatter is None:
        return
    names: dict[str, str] = {}
    results: list[ReplyToolResult] = []
    for message in ctx.tool_chain:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call.get("id", "")] = call.get("function", {}).get("name", "")
        elif message.get("role") == "tool":
            content = message.get("content")
            results.append(ReplyToolResult(
                names.get(message.get("tool_call_id", ""), ""),
                parse_tool_result_json(content) if isinstance(content, str) else None,
            ))
    try:
        text = formatter(results)
        target = target_from_anything(ctx.anything, ctx.adapter_key)
        if text and target is not None:
            # 无论发送是否成功，都不能把内部独白作为这个回执的替代品发出。
            state.pending_text = ""
            state.output_sent = await deliver_text(target, text)
            ctx.execution_steps.append("→ 频道结果回执" + ("已送达" if state.output_sent else "发送失败"))
    except Exception as exc:
        state.pending_text = ""
        log(f"频道结果回执失败: {exc}", "WARNING", tag="思维")


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
        await _finish_round(ctx, state, deliver_pending=False, allow_receipt=False)
        return _StageOutcome.BREAK
    # 本轮 tool_calls 因安全原因一并丢弃，显式告知 LLM 避免下轮误以为已执行
    prompt = _PROMPT_SECURITY_LEAK
    if tool_calls:
        prompt += "本轮的工具调用请求因安全检测一并被丢弃，请在下轮重新发起。"
    ctx.tool_chain.append({"role": "system", "content": prompt})
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 安全泄露已拦截并纠正")
    state.iteration += 1
    return _StageOutcome.CONTINUE


async def _handle_text_only_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
) -> _StageOutcome:
    """纯文本轮：文本不是终局——追加为独白，循环继续，轮末统一投递。

    分支：空输出 / [SILENT] 沉默 / 后台任务等待挂起 / 反思收束 / 连续独白停滞掐断。
    """
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    raw_text = _strip_think_blocks(result.content or "").strip()

    if not raw_text:
        # 空输出：可接受（思考中/无意回复），不注入纠正提示；
        # 连续 2 次空输出安静结束本轮
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
        # [SILENT] 精确匹配 / 幻觉沉默旁白：AI 决定不回复，暂存独白一并丢弃，直接结束本轮
        log(f"AI 选择沉默（{raw_text[:30]}），结束本轮", "DEBUG", tag="思维")
        _append_assistant_msg(tool_chain, result, raw_text)
        execution_steps.append(f"→ 第{state.iteration + 1}轮: AI 选择沉默，结束")
        state.pending_text = ""
        await _finish_round(ctx, state, deliver_pending=False)
        return _StageOutcome.BREAK
    else:
        state.consecutive_empty_calls = 0
        # REPLY：弱模型把 end_reply 写成文本——按结束意图处理，并以规范
        # function calling 形态入链（assistant tool_calls + tool 结果）：
        # 幻觉文本本身不留痕，上下文与执行摘要看到的都是正确格式，不强化文本形态调用
        if ctx.mode == ThinkMode.REPLY and _END_REPLY_TEXT_RE.match(raw_text):
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": _SYNTHETIC_END_REPLY_CALL_ID,
                    "type": "function",
                    "function": {"name": _END_REPLY_TOOL_NAME, "arguments": "{}"},
                }],
            }
            preserve_reasoning_fields(assistant_msg, result, tool_turn=True)
            tool_chain.append(assistant_msg)
            tool_chain.append({
                "role": "tool",
                "tool_call_id": _SYNTHETIC_END_REPLY_CALL_ID,
                "content": '{"ok": true, "action": "end_reply"}',
            })
            log("文本形态的 end_reply，按结束意图规范入链并结束（不投递）", "WARNING", tag="思维")
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 文本形态 end_reply，按结束处理"
            )
            state.pending_text = ""
            await _finish_round(ctx, state, deliver_pending=False)
            return _StageOutcome.BREAK
        _append_assistant_msg(tool_chain, result, raw_text)
        ctx.collected_text.append(raw_text)

        bg = ctx.background
        running_bg: List[BackgroundTaskInfo] = []
        if ctx.mode == ThinkMode.REPLY and ctx.anything and bg is not None:
            running_bg = bg.running(ctx.current_scope)

        if bg is not None and ctx.anything is not None and running_bg and state.wait_budget > 0:
            # 等待挂起：后台任务运行中时的纯文本一律视为等待——挂起会合
            # （结构性判定，不解析文本语义）。
            # 挂起期间新消息照常实时入库，中断/新消息/完成/超时都会安全唤醒；
            # 超时说明等待无望，清零预算，后续纯文本回落到独白路径。
            reason, completions, elapsed = await _suspend_for_background(
                ctx.mind, ctx.anything, bg, ctx.current_scope,
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
                        completions, bg.running(ctx.current_scope),
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

        if ctx.mode == ThinkMode.REFLECT:
            # 反思模式：连续纯文本达到上限即收束（产出已累积在 collected_text）；
            # 收束边界先消费 after 档追加指令——有后续要求则续跑而非结束
            state.reflect_text_rounds += 1
            if state.reflect_text_rounds >= _MAX_REFLECT_TEXT_ROUNDS:
                if merge_after_messages(ctx, state):
                    state.reflect_text_rounds = 0
                    state.iteration += 1
                    return _StageOutcome.CONTINUE
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
        else:
            # REPLY：纯文本 = 独白，不终局不投递；暂存为未送达文本，强制收尾时统一投递。
            # 连续独白达到上限说明模型一直不调用工具——掐断，经 _finish_round 投递收尾
            state.pending_text = raw_text
            state.consecutive_text_rounds += 1
            if state.consecutive_text_rounds >= ctx.mind._get_mind_config().text_without_tool_limit:
                log(
                    f"连续 {state.consecutive_text_rounds} 轮纯文本未调用工具，掐断并投递收尾",
                    "WARNING", tag="思维",
                )
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 连续独白 {state.consecutive_text_rounds} 轮"
                    "（未调用工具），掐断结束"
                )
                await _finish_round(ctx, state)
                return _StageOutcome.BREAK
            execution_steps.append(f"→ 第{state.iteration + 1}轮: 纯文本独白（未投递，等待动作）")

    state.iteration += 1
    return _StageOutcome.CONTINUE


async def _handle_tool_round(
        ctx: _ThinkLoopCtx,
        state: _ThinkRoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
        *,
        early_runner: Optional["_EarlyToolRunner"] = None,
) -> _StageOutcome:
    """工具执行轮：执行工具批次、全错升级与 end_reply 结束拦截。"""
    from agent.mind.autonomous import MindPhase

    mind = ctx.mind
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    guardrail = ctx.guardrail

    mind._set_phase(MindPhase.TOOL_EXECUTING)
    state.consecutive_empty_calls = 0
    state.consecutive_text_rounds = 0
    state.reflect_text_rounds = 0
    # 反思产出语义：模型发起工作工具调用即说明此前的纯文本是中间独白而非
    # 最终结论——从产出中移除（过程留痕进 execution_steps），产出只保留
    # 收束前最后一个未被工具调用打断的连续文本段。
    # end_reply 是收束信号而非工作工具：纯 end_reply 批次不构成"打断"，
    # 已收集的连续文本段即最终结论，必须保留（同批正文在下方收束处理纳入）
    pure_end_reply = all(tc.name == _END_REPLY_TOOL_NAME for tc in tool_calls)
    if ctx.mode == ThinkMode.REFLECT and ctx.collected_text and not pure_end_reply:
        dropped_chars = sum(len(s) for s in ctx.collected_text)
        execution_steps.append(
            f"→ 第{state.iteration + 1}轮: 中间独白 {dropped_chars} 字归档为过程"
            "（产出以最终总结段为准）"
        )
        ctx.collected_text.clear()
    partial_abort = await execute_tool_calls(
        mind, tool_chain, result, tool_calls, state.iteration, ctx.anything,
        guardrail=guardrail, pipeline=ctx.pipeline, blocked_tools=ctx.blocked_tools,
        abort_event=ctx.abort_event, early=early_runner, journal=ctx.journal,
    )
    if partial_abort:
        state.partial_tool_abort = True

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
    mind.pfc.expand_discovered_tools(tool_calls, scope=ctx.current_scope if ctx.mode == ThinkMode.REPLY else "")

    tool_names = ", ".join(tc.name for tc in tool_calls)
    execution_steps.append(f"→ 第{state.iteration + 1}轮: 调用工具 [{tool_names}]")

    called = {tc.name for tc in tool_calls}

    # 输出类工具成功送达后，此前暂存的独白文本已被正式回复取代；
    # 登记本轮已有送达，轮末纯文本不再兜底投递
    if _round_output_sent_successfully(tool_chain, tool_calls):
        state.pending_text = ""
        state.output_sent = True

    # 非输出工具伴随文本独白时提醒"结果仅自己可见"——独白是模型误以为
    # 文字可达用户的信号；纯工具轮无需提示（exec_context 每轮已有输出契约）
    if ctx.mode == ThinkMode.REPLY:
        round_had_text = bool(_strip_think_blocks(result.content or "").strip())
        if (
            round_had_text
            and not (called & _OUTPUT_TOOL_NAMES)
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
        # 收束边界：子代理本要结束时收到的追加指示（after 档）优先于收束——
        # 注入续跑而非随收束清箱丢弃（对齐 pi followUp 档：仅在即将停止时投递）
        if merge_after_messages(ctx, state):
            state.reflect_text_rounds = 0
            state.iteration += 1
            return _StageOutcome.CONTINUE
        # end_reply 即静默收束：REPLY 下同批正文与暂存独白一律不投递
        # （回复走 send_message，结束备注写 reason 仅内部日志）；REFLECT 下
        # 收束信号不是工作工具，同批文本即最终连续文本段，纳入产出
        end_text = _strip_think_blocks(result.content or "").strip()
        if ctx.mode == ThinkMode.REFLECT and ctx.require_output and not end_text and not ctx.collected_text:
            ctx.summary_only = True
            tool_chain.append({
                "role": "system",
                "content": (
                    "你已请求结束，但尚未提交最终总结。下一轮只根据已有工具结果补交总结，"
                    "有输出契约则提交对应 JSON。所有工具调用均已禁用，不能继续或重做操作；"
                    "未验证的事项如实标为未确认，不得推测成功。直接输出总结正文，不能只调用 end_reply。"
                ),
                "_source": {"origin": "output_recovery"},
            })
            execution_steps.append(f"→ 第{state.iteration + 1}轮: 缺少最终总结，补交一轮（禁用操作）")
            state.iteration += 1
            return _StageOutcome.CONTINUE
        if ctx.mode == ThinkMode.REPLY:
            dropped_chars = len(end_text) + len(state.pending_text)
            state.pending_text = ""
            if dropped_chars:
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: end_reply 静默收束，"
                    f"{dropped_chars} 字未投递文本已丢弃"
                )
        elif end_text:
            ctx.collected_text.append(end_text)
        log(f"AI 主动结束{ctx.mode_label} (轮次 {state.iteration + 1})", tag="思维")
        # Plan 收敛由 finish_think 统一处理（所有正常结束路径的必经之地）
        await _finish_round(ctx, state, deliver_pending=False)
        return _StageOutcome.BREAK

    state.iteration += 1
    return _StageOutcome.CONTINUE


# ==================================================================
# 工具执行
# ==================================================================

class _EarlyToolRunner:
    """流中提前执行只读工具调用：item 完成即分发，结果由工具轮按 id 回收。

    提前分发只对 concurrency_safe（只读）调用开放——写操作的顺序语义
    （投递次序、审批序）由工具批的串行路径保证。预检与工具批同源
    （模式禁用/守卫/参数泄露）；提前结果在工具批按 id 对账复用，参数
    漂移即弃用提前结果改为现场执行（协议回补片段的兜底）。
    """

    def __init__(
            self,
            mind: "Mind",
            anything: Optional["Everything"],
            iteration: int,
            *,
            blocked_tools: AbstractSet[str] = frozenset(),
            guardrail: Optional["GuardrailController"] = None,
    ) -> None:
        self._mind = mind
        self._anything = anything
        self._iteration = iteration
        self._blocked_tools = blocked_tools
        self._guardrail = guardrail
        self._semaphore = asyncio.Semaphore(_MAX_TOOL_CONCURRENCY)
        self._tasks: Dict[str, asyncio.Task] = {}
        self._calls: Dict[str, "ToolCall"] = {}
        self._started: Set[str] = set()
        self._closed = False

    def submit(self, tc: "ToolCall") -> None:
        """流中收到完整调用时评估并分发；不合资格则忽略，留给工具批。"""
        if self._closed or tc.id in self._tasks:
            return
        if not self._is_readonly(tc.name):
            return
        if _precheck_tool_call(self._mind, tc, self._blocked_tools, self._guardrail) is not None:
            return
        # 参数复述会话令牌的调用不提前执行：泄露轮的整批丢弃语义保持在
        # 工具批层面统一裁决
        if tc.arguments:
            from agent.security.session_token import detect_leak
            if detect_leak(tc.arguments):
                return
        self._calls[tc.id] = tc
        self._started.add(tc.id)
        self._tasks[tc.id] = asyncio.create_task(self._run(tc))

    async def _run(self, tc: "ToolCall") -> str:
        async with self._semaphore:
            return await execute_one_tool(self._mind, tc, self._iteration, self._anything)

    async def collect(self, tc: "ToolCall") -> Any:
        """工具批回收提前结果；未提前/已放弃/参数漂移返回 _EARLY_MISS。

        返回值为原始输出（str）或异常实例——异常由工具批的归因映射统一
        处理，与现场执行同一路径。
        """
        task = self._tasks.get(tc.id)
        if task is None:
            return _EARLY_MISS
        early = self._calls[tc.id]
        if early.name != tc.name or early.arguments != tc.arguments:
            return _EARLY_MISS
        if task.cancelled():
            return _EARLY_MISS
        try:
            return await task
        except asyncio.CancelledError:
            # 等待期间本协程被取消（批次中断）：照常向上传播
            raise
        except BaseException as e:
            return e

    def abandon(self, *, cancel: bool = False) -> None:
        """关闭提交入口；未被工具批回收的任务静默收尾（cancel=True 时中止）。

        中断路径 cancel=True（只读调用，中止无副作用风险）；轮次正常
        结束后残留任务（结果被丢弃的轮次）让其自然完成并消费终态。
        """
        self._closed = True
        for task in self._tasks.values():
            if cancel and not task.done():
                task.cancel()
            if not task.done():
                task.add_done_callback(_consume_task_result)

    def executing_ids(self) -> Set[str]:
        """已开始执行的调用 id 集（含提前分发者，中断占位语义用）。"""
        return set(self._started)

    def _is_readonly(self, name: str) -> bool:
        """与 _partition_tool_calls 同源的只读判定（fail-closed）。"""
        try:
            from core.entity import EntityRegistry
            entity = EntityRegistry.get(name)
            return bool(entity and entity.meta.get("concurrency_safe"))
        except Exception:
            return False


# collect 的未命中哨兵：区分"未提前执行"与"提前结果为空串"
_EARLY_MISS = object()


def _consume_task_result(task: asyncio.Task) -> None:
    """消费已完成任务的终态，防 fire-and-forget 未等待告警。"""
    if task.cancelled():
        return
    if task.exception() is not None:
        log(f"提前执行任务异常: {task.exception()}", "DEBUG", tag="思维")


def _precheck_tool_call(
        mind: Mind,
        tc: "ToolCall",
        blocked_tools: Optional[AbstractSet[str]],
        guardrail: Optional["GuardrailController"],
) -> Optional[str]:
    """执行前预检（模式禁用/守卫）；拦截时返回合成结果文本，放行返回 None。

    工具批与流中提前分发共用，保证两条路径的拦截语义一致。
    """
    if blocked_tools and tc.name in blocked_tools:
        from core.tool_errors import ErrorCause, tool_error
        log(f"模式禁用工具拦截: {tc.name}", "DEBUG", tag="思维")
        return tool_error(
            f"工具 {tc.name} 在当前模式（内部任务/受限角色）下不可用",
            cause=ErrorCause.PERMISSION, retryable=False,
            hint="该工具仅被限制而非必需：请改用允许的工具完成任务，勿重复调用",
        )
    if guardrail is not None:
        decision = guardrail.before_call(tc.name, tc.arguments or "")
        if decision.should_block:
            from agent.mind.guardrails import synthetic_block_result
            log(f"工具守卫拦截: {tc.name} ({decision.reason})", "WARNING", tag="思维")
            return synthetic_block_result(decision)
    return None


async def _await_with_abort(aw: Any, abort_event: Optional[asyncio.Event]) -> tuple[Any, bool]:
    """await 一个可等待对象并与中断事件竞争；返回 (结果, 是否中断)。

    中断胜出时在途协程已被取消并收尸（gather 会级联取消子任务）。
    """
    if abort_event is None:
        return await aw, False
    task = asyncio.ensure_future(aw)
    abort_task = asyncio.ensure_future(abort_event.wait())
    done, _ = await asyncio.wait(
        {task, abort_task}, return_when=asyncio.FIRST_COMPLETED,
    )
    if abort_task in done and task not in done:
        task.cancel()
        await suppress_task(task)
        return None, True
    abort_task.cancel()
    return task.result(), False


def _aborted_tool_result(tc: "ToolCall", started: AbstractSet[str]) -> str:
    """中断占位结果：区分"未执行"与"执行中被中止（可能已部分执行）"。"""
    from core.tool_errors import ErrorCause, tool_error
    if tc.id in started:
        return tool_error(
            "用户中断，该调用执行中被中止（可能已部分执行）",
            cause=ErrorCause.USER_CANCEL, retryable=False,
            hint="继续前请先核对实际状态，如需重做请在下轮重新发起",
        )
    return tool_error(
        "用户中断，该调用未执行",
        cause=ErrorCause.USER_CANCEL, retryable=False,
        hint="如需继续请在下轮重新发起该调用",
    )


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
        abort_event: Optional[asyncio.Event] = None,
        early: Optional[_EarlyToolRunner] = None,
        journal: Optional["ReplyToolJournal"] = None,
) -> bool:
    """执行工具调用并将 assistant + tool 消息追加到 tool_chain。

    返回是否存在"执行中被中止"的调用（中断路径的占位结果提示可能已
    部分执行，供收束元消息措辞使用）。

    并发边界（concurrency_safe 的契约）：
    - 并发段仅为 execute_one_tool（审批闸 + 工具体 + 事件/hook）——连续的
      concurrency_safe 调用经信号量限流并行，结果加工/链拼装/多模态注入
      始终在父任务按调用序串行；流中提前分发（early）受同规格信号量约束，
      且仅限只读调用。
    - 框架层共享设施为并发安全：事件总线单线程交错、ContextVar 随任务
      拷贝隔离（工具内上下文变更不串入兄弟任务）、人工审批段经闸内锁
      串行呈现；用户 hook（tool_pre/post）会随并行调用并发拉起，hook
      脚本须自身容忍并发。
    - 标注责任：内置工具由作者声明（只读才可开）；MCP 工具由服务器
      readOnlyHint 声明自动映射，写操作保持串行（fail-closed）。

    中断语义：每个批次与 scope 中断事件竞争，中断到达即取消在批任务、
    剩余调用以占位结果落链（配对铁律不破）；已完成的真实结果保留。

    保留 content 和推理字段以维持多轮思维链连续性。
    实际发送内容由工具（如 send_message）的 _record_to_context 负责写入 DB。
    结果加工（脱敏/扫描/守卫/截断）由 ToolResultPipeline 统一处理。
    blocked_tools 为模式级禁用工具：执行侧拦截返回合成错误（可见性与权限分离）。
    journal 非空时逐调用落账（REPLY 模式的崩溃尾部账本，见 reply_journal）。
    """
    if pipeline is None:
        pipeline = ToolResultPipeline(mind, guardrail)
    pipeline.begin_turn()

    # 配对铁律：assistant 与其全部 tool 响应必须原子落链
    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "content": _strip_think_blocks(result.content or ""),
        "tool_calls": [tc.raw for tc in tool_calls],
    }
    preserve_reasoning_fields(assistant_msg, result, tool_turn=True)

    # 守卫执行前检查：已知必败/无进展的调用直接返回合成结果，不执行真实工具
    # 模式级禁用工具（内部任务禁外发等）同样在此拦截：schema 保留在数组中
    # 保持前缀缓存一致，执行侧统一兜底（可见性与权限分离）
    blocked_results: Dict[str, str] = {}
    for tc in tool_calls:
        blocked = _precheck_tool_call(mind, tc, blocked_tools, guardrail)
        if blocked is not None:
            blocked_results[tc.id] = blocked

    async def _run_one(tc: ToolCall) -> str:
        if tc.id in blocked_results:
            return blocked_results[tc.id]
        if early is not None:
            early_output = await early.collect(tc)
            if early_output is not _EARLY_MISS:
                return early_output
        return await execute_one_tool(mind, tc, iteration, anything)

    # 并发安全分级：连续只读调用并行（上限 10），写操作严格串行。
    # 无论哪条路径，tool 消息都按 tool_calls 原始顺序累积，保证配对完整。
    semaphore = asyncio.Semaphore(_MAX_TOOL_CONCURRENCY)
    started: Set[str] = set()
    finished: Dict[str, Any] = {}

    async def _run_guarded(tc: ToolCall):
        async with semaphore:
            started.add(tc.id)
            try:
                output = await _run_one(tc)
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                finished[tc.id] = e
                return e
            finished[tc.id] = output
            return output

    if early is not None:
        started |= early.executing_ids()

    # 局部累积本批次的工具结果与多模态注入消息：
    # tool 结果必须整体连续（Anthropic tool_result 邻接、Responses 端点要求
    # output 紧邻 function_call），多模态 user 消息只落在全部结果之后的尾部
    batch_msgs: List[Dict] = []
    multimodal_msgs: List[Dict] = []

    async def _emit(tc: ToolCall, output: Any) -> None:
        """单调用结果加工落链（真实输出/异常统一归因映射）+ 多模态展开 + 落账。"""
        was_exception = isinstance(output, BaseException)
        if was_exception:
            # 统一走归因映射（超时/网络/权限…），不裸抛 str(exc)
            output = error_from_exception(output, action=f"工具 {tc.name} 执行")
        output_str = output if isinstance(output, str) else str(output)
        process_failed = False
        try:
            final_output = pipeline.process(
                tc.name, tc.arguments or "", output_str,
                skip_guardrail=tc.id in blocked_results,
            )
        except Exception as e:
            # 配对铁律：结果加工失败也要保证 tool 消息落链
            process_failed = True
            final_output = error_from_exception(e, action=f"工具 {tc.name} 结果加工")
        batch_msgs.append({"role": "tool", "tool_call_id": tc.id, "content": final_output})
        # 多模态工具结果：候选图片注入上下文，让视觉模型直接看到
        try:
            await _append_multimodal_result(mind, multimodal_msgs, final_output)
        except Exception as exc:
            log(f"多模态工具结果展开失败（不影响主流程）: {exc}", "DEBUG", tag="思维")
        if journal is not None:
            status = (
                "blocked" if tc.id in blocked_results
                else ("error" if was_exception or process_failed else "ok")
            )
            await journal.record(tc.name, tc.arguments or "", final_output, status=status)

    aborted = False
    for is_parallel, batch in _partition_tool_calls(tool_calls):
        if aborted or (abort_event is not None and abort_event.is_set()):
            aborted = True
            for tc in batch:
                batch_msgs.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": _aborted_tool_result(tc, started),
                })
            continue
        if is_parallel and len(batch) > 1:
            outputs, hit = await _await_with_abort(
                asyncio.gather(*[_run_guarded(tc) for tc in batch]), abort_event,
            )
            if hit:
                aborted = True
                for tc in batch:
                    if tc.id in finished:
                        await _emit(tc, finished[tc.id])
                    else:
                        batch_msgs.append({
                            "role": "tool", "tool_call_id": tc.id,
                            "content": _aborted_tool_result(tc, started),
                        })
                continue
            for tc, output in zip(batch, outputs, strict=False):
                await _emit(tc, output)
        else:
            # 串行批逐个执行（写操作的顺序语义），逐调用与中断竞争
            for tc in batch:
                if aborted:
                    batch_msgs.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": _aborted_tool_result(tc, started),
                    })
                    continue
                output, hit = await _await_with_abort(_run_guarded(tc), abort_event)
                if hit:
                    aborted = True
                    batch_msgs.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": _aborted_tool_result(tc, started),
                    })
                    continue
                await _emit(tc, output)

    if aborted and early is not None:
        early.abandon(cancel=True)

    # 原子落链
    tool_chain.append(assistant_msg)
    tool_chain.extend(batch_msgs)
    tool_chain.extend(multimodal_msgs)
    log_tool_round(iteration, tool_calls)
    # 存在"已启动但未完成"的调用 = 中断中止了在途执行（可能部分执行）
    return aborted and any(
        tc.id in started and tc.id not in finished for tc in tool_calls
    )


def _record_tool_result_failure(mind: Mind, tc: ToolCall, result: str) -> None:
    """工具"礼貌失败"（正常返回 tool_error JSON）的选择性落库。

    只把工具/环境侧真实故障（permission/config/network/timeout/state/internal）
    记入错误台账供反思；AI 自身试错（param/not_found/user_cancel）不入表，
    保持 recall_tool_errors 的信噪比。fire-and-forget：落库失败不影响结果。
    """
    store = mind.memory_store
    if store is None or not result:
        return
    text = result.strip()
    if not text.startswith("{"):
        return
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict) or "error" not in payload:
        return
    cause = str(payload.get("cause", "") or "").strip().lower()
    _TRACKED_FAILURE_CAUSES = {
        "permission", "config", "network", "timeout", "state", "internal",
    }
    if cause not in _TRACKED_FAILURE_CAUSES:
        return

    async def _record() -> None:
        try:
            await store.record_tool_error(
                tool_name=tc.name,
                error_type=cause,
                error_msg=str(payload.get("error", ""))[:300],
                args_json=(tc.arguments or "")[:500],
            )
        except Exception:
            pass

    from core.async_helper import spawn
    spawn(_record(), name=f"tool_error.{tc.name}")


async def execute_one_tool(
        mind: Mind,
        tc: ToolCall,
        iteration: int,
        anything: Optional[Everything] = None,
) -> str:
    """执行单个工具调用。"""
    from agent.mind.autonomous import MindPhase
    from core.log import current_log_actor
    from core.tool_context import request_trace

    mind._set_phase(MindPhase.TOOL_EXECUTING)
    tool_scope = getattr(anything, "entity_scope", "") if anything is not None else ""
    await event_bus.emit(EVENT_TOOL_EXECUTED, {"tool": tc.name, "iteration": iteration})
    await event_bus.emit(EVENT_THINKING_TOOL_START, {
        "actor": current_log_actor(),
        "request": request_trace(),
        "scope": tool_scope,
        "tool_name": tc.name,
        "tool_id": tc.id,
        "arguments_preview": tc.arguments[:300] if tc.arguments else "",
        "iteration": iteration,
    })
    log(f"执行工具: {tc.name}", tag="思维")

    # 批准机制：执行前审批检查（对话/反思/子代理统一求值）
    denied = await _request_tool_approval(tc, anything, tool_scope, mind)
    if denied is not None:
        return denied

    t0 = time.time()
    try:
        result = await mind.tool_executor(tc)  # type: ignore[misc]
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "actor": current_log_actor(),
            "request": request_trace(),
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
        _record_tool_result_failure(mind, tc, result)
        return result
    except Exception as exc:
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "actor": current_log_actor(),
            "request": request_trace(),
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


def log_tool_round(iteration: int, tool_calls: List[ToolCall]) -> None:
    log(
        f"第 {iteration + 1} 轮工具调用: "
        f"{', '.join(tc.name for tc in tool_calls)}",
        tag="思维",
    )
