"""工具批次预检、并发执行、中断与结果落账。"""


from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, AbstractSet, Any, Dict, List, Optional, Set

from agent.context.message_schema import preserve_reasoning_fields
from agent.mind.execution.permission import check_tool_permission
from agent.mind.execution.responses import _strip_think_blocks
from agent.mind.execution.result_pipeline import ToolResultPipeline
from agent.mind.execution.vision import _append_multimodal_result
from agent.mind.mind_tools import END_REPLY_TOOL_NAME
from core.event_bus import (
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
    from agent.mind.execution.reply_journal import ReplyToolJournal
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind


_MAX_TOOL_CONCURRENCY = 10


def _partition_tool_calls(tool_calls: List["ToolCall"]) -> List[tuple]:
    """按并发安全性把工具调用切分为连续批次。

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
        if tc.name == END_REPLY_TOOL_NAME:
            self._closed = True
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
    try:
        done, _ = await asyncio.wait({task, abort_task}, return_when=asyncio.FIRST_COMPLETED)
        if abort_task in done and task not in done:
            return None, True
        return task.result(), False
    finally:
        for pending in (task, abort_task):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, abort_task, return_exceptions=True)


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
    ended = False
    for tc in tool_calls:
        if ended:
            from core.tool_errors import ErrorCause, tool_error
            blocked_results[tc.id] = tool_error("end_reply 之后的调用未执行", cause=ErrorCause.USER_CANCEL, retryable=False)
            continue
        ended = tc.name == END_REPLY_TOOL_NAME
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
        if tc.id in blocked_results or process_failed:
            await event_bus.emit(EVENT_THINKING_TOOL_END, {
                "tool_id": tc.id, "tool_name": tc.name, "arguments": tc.arguments or "",
                "result": final_output, "success": False, "duration_ms": 0,
            })
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
    from core.activity import ACTIVITY_TEXT_LIMIT
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
        "arguments": (tc.arguments or "")[:ACTIVITY_TEXT_LIMIT],
        "arguments_truncated": len(tc.arguments or "") > ACTIVITY_TEXT_LIMIT,
        "iteration": iteration,
    })
    log(f"执行工具: {tc.name}", tag="思维")

    t0 = time.time()
    try:
        denied = await check_tool_permission(tc, anything, tool_scope, mind)
        result = denied if denied is not None else await mind.tool_executor(tc)  # type: ignore[misc]
        from core.tool_results import extract_error_text
        result_error = extract_error_text(result)
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "actor": current_log_actor(),
            "request": request_trace(),
            "scope": tool_scope,
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "result_preview": result[:300] if result else "",
            "result": (result or "")[:ACTIVITY_TEXT_LIMIT],
            "result_truncated": len(result or "") > ACTIVITY_TEXT_LIMIT,
            "error": result_error,
            "success": not bool(result_error),
        })
        if denied is not None:
            return denied
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
        failure = error_from_exception(exc, action=f"工具 {tc.name} 执行")
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "actor": current_log_actor(),
            "request": request_trace(),
            "scope": tool_scope,
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "error": str(exc),
            "result": failure,
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
        return failure


def log_tool_round(iteration: int, tool_calls: List[ToolCall]) -> None:
    log(
        f"第 {iteration + 1} 轮工具调用: "
        f"{', '.join(tc.name for tc in tool_calls)}",
        tag="思维",
    )
