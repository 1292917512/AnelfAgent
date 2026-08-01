"""委托管理器 — 子代理的并发调度、预算控制与结果聚合。

- 并发上限：顶层与嵌套委托各一把 asyncio.Semaphore（默认各 3，可配置），
  嵌套委托不竞争顶层槽位以避免持槽等待死锁；获取槽位带超时
- 并行模式：tasks 数组 fan-out，asyncio.gather 并发执行
- 预算控制：每个子代理独立的迭代预算（默认 15 轮）
- 结果聚合：按 task_index 排序，摘要按父上下文剩余空间动态截断
- 后台模式：登记 BackgroundTaskRegistry 后立即返回 delegation_id，
  结果按注册表路由（轮内会合注入 / 完成即新 turn 通知）
- 事件发射：``EVENT_DELEGATION_STARTED`` / ``EVENT_DELEGATION_PROGRESS`` /
  ``EVENT_DELEGATION_RESOLVED`` —— 前端据此渲染 DelegationCard 实时进度。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List

from agent.delegation.sub_agent import (
    SubAgent,
    SubAgentResult,
    bind_delegation_id,
    current_delegation_id,
    current_depth,
    normalize_role,
    reset_delegation_id,
)
from core.event_bus import (
    EVENT_DELEGATION_PROGRESS,
    EVENT_DELEGATION_RESOLVED,
    EVENT_DELEGATION_STARTED,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

EVENT_DELEGATION_COMPLETED = "delegation.completed"

# scope 工具与 plan 模块共享（统一实现，避免逐字重复）
from agent.planning.tracker import (  # noqa: E402
    current_scope as _current_scope,
)
from agent.planning.tracker import (
    parse_scope_chat_id as _parse_scope_chat_id,
)

# 结果摘要预算（参考 hermes：父上下文剩余空间的 50% 均分给各子任务）
_SUMMARY_HEADROOM_FRACTION = 0.5
_MIN_SUMMARY_CHARS = 2_000
_MAX_SUMMARY_CHARS = 24_000
_CHARS_PER_TOKEN = 4
# 完成通知 / 事件中的摘要截断长度
_SUMMARY_NOTICE_MAX_CHARS = 1_500
_RESOLVED_OUTPUT_PREVIEW_CHARS = 2_000
# 摘要截断保留比例（头部 75% + 尾部 25%）
_TRIM_HEAD_FRACTION = 0.75

# 用户取消的取消消息（写入工具结果，引导 AI 不要自动重试）
CANCELLED_MESSAGE = (
    "该子代理任务已被用户手动取消（cancelled_by_user）。"
    "这是用户的主动决定，请勿自动重试该任务；如需继续，等待用户进一步指示。"
)


def _cancelled_result(
        goal: str,
        *,
        role: str = "leaf",
        task_index: int = 0,
) -> SubAgentResult:
    """构造用户取消的子代理结果。"""
    return SubAgentResult(
        goal=goal, success=False, error=CANCELLED_MESSAGE,
        role=normalize_role(role), task_index=task_index, cancelled=True,
    )


def _max_concurrent() -> int:
    from core.config import get_config_int
    return max(1, get_config_int("delegation_max_concurrent", 3))


def _acquire_timeout_seconds() -> float:
    from core.config import get_config_float
    return max(1.0, get_config_float("delegation_acquire_timeout_seconds", 300.0))


class DelegationManager:
    """子代理委托管理器。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        self._semaphore = asyncio.Semaphore(_max_concurrent())
        # 嵌套委托（orchestrator 子代理内再委托）使用独立信号量，
        # 避免 orchestrator 持有顶层槽位等待同一把信号量造成死锁
        self._nested_semaphore = asyncio.Semaphore(_max_concurrent())
        self._background_tasks: Dict[str, asyncio.Task] = {}
        # 运行中委托的实时信息（进度事件归属 / 取消 / 运行快照）
        self._running: Dict[str, Dict[str, Any]] = {}
        # 已请求取消但尚未进入执行阶段的委托 ID（并发槽等待中取消的场景）
        self._cancel_marks: set[str] = set()
        self._install_progress_hook()

    # ------------------------------------------------------------------
    # 进度事件（子代理运行期 → 前端 DelegationCard 实时进度）
    # ------------------------------------------------------------------

    def _install_progress_hook(self) -> None:
        """订阅思维循环的轮次/工具事件，转译为 delegation_progress。

        event_bus 处理器在发射方上下文中内联执行，因此经 ContextVar 读取
        当前委托 ID 即可把子代理的内部活动归属到对应委托卡片。
        """
        from core.event_bus import (
            EVENT_THINKING_REPLY_ROUND,
            EVENT_THINKING_TOOL_END,
            EVENT_THINKING_TOOL_START,
        )

        async def _on_round(payload: Dict[str, Any]) -> None:
            await self._emit_progress("round", iteration=int(payload.get("iteration", 0)))

        async def _on_tool_start(payload: Dict[str, Any]) -> None:
            await self._emit_progress("tool_start", tool=str(payload.get("tool_name", "")))

        async def _on_tool_end(payload: Dict[str, Any]) -> None:
            await self._emit_progress(
                "tool_end",
                tool=str(payload.get("tool_name", "")),
                success=bool(payload.get("success")),
            )

        event_bus.on(EVENT_THINKING_REPLY_ROUND, _on_round, owner="delegation")
        event_bus.on(EVENT_THINKING_TOOL_START, _on_tool_start, owner="delegation")
        event_bus.on(EVENT_THINKING_TOOL_END, _on_tool_end, owner="delegation")

    async def _emit_progress(self, kind: str, **fields: Any) -> None:
        """子代理内部活动 → delegation_progress 事件（仅运行中的委托）。"""
        delegation_id = current_delegation_id()
        if not delegation_id:
            return
        info = self._running.get(delegation_id)
        if info is None:
            return
        try:
            await event_bus.emit(EVENT_DELEGATION_PROGRESS, {
                "scope": info["scope"],
                "chat_id": info["chat_id"],
                "delegation_id": delegation_id,
                "kind": kind,
                "ts": time.time(),
                **fields,
            })
        except Exception:
            log("delegation_progress 发射异常已忽略", "DEBUG")

    # ------------------------------------------------------------------
    # 取消与运行快照（webui 停止按钮 / DelegationCard 取消）
    # ------------------------------------------------------------------

    def cancel(self, delegation_id: str) -> bool:
        """取消运行中的委托（用户主动触发）。返回是否找到该委托。"""
        info = self._running.get(delegation_id)
        task = self._background_tasks.get(delegation_id)
        if info is None and task is None:
            return False
        self._cancel_marks.add(delegation_id)
        if info is not None:
            run_task = info.get("task")
            if isinstance(run_task, asyncio.Task) and not run_task.done():
                run_task.cancel()
        if task is not None and not task.done():
            task.cancel()
        log(f"委托取消请求: {delegation_id}", tag="委托")
        return True

    def cancel_scope(self, scope: str) -> int:
        """取消指定会话 scope 下所有运行中的委托，返回取消数量。"""
        targets = [
            did for did, info in self._running.items()
            if info.get("scope") == scope
        ]
        for did in targets:
            self.cancel(did)
        return len(targets)

    def running_snapshot(self, scope: str) -> List[Dict[str, Any]]:
        """指定 scope 下运行中的委托快照（前端刷新后恢复卡片用）。"""
        now = time.time()
        return [
            {
                "delegation_id": did,
                "goal": str(info.get("goal", "")),
                "role": str(info.get("role", "leaf")),
                "task_index": int(info.get("task_index", 0)),
                "background": bool(info.get("background")),
                "elapsed_seconds": int(now - float(info.get("started_at", now))),
            }
            for did, info in self._running.items()
            if info.get("scope") == scope
        ]

    # ------------------------------------------------------------------
    # 同步委托
    # ------------------------------------------------------------------

    async def delegate(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            task_index: int = 0,
            scope_hint: str = "",
    ) -> SubAgentResult:
        """委托单个子任务（阻塞至完成）。

        子代理在独立 asyncio.Task 中执行并登记到 _running：
        - 进度事件经 ContextVar 归属到本委托（前端实时进度）
        - cancel() 取消该 Task 时转化为"用户取消"结果返回给调用方，
          而不是让 CancelledError 击穿父级思维循环
        """
        # 顶层委托（depth 0）与嵌套委托（depth>=1）分离并发槽，
        # 嵌套方持槽等待时不再竞争同一把信号量
        semaphore = self._semaphore if current_depth() < 1 else self._nested_semaphore
        timeout = _acquire_timeout_seconds()
        delegation_id = uuid.uuid4().hex[:8]
        scope = scope_hint or _current_scope()
        _user_scope, chat_id = _parse_scope_chat_id(scope)

        # 发射 started 事件（前端 DelegationCard 渲染）
        try:
            await event_bus.emit(EVENT_DELEGATION_STARTED, {
                "scope": scope,
                "chat_id": chat_id,
                "delegation_id": delegation_id,
                "goal": goal,
                "context_preview": context[:200],
                "role": normalize_role(role),
                "task_index": task_index,
                "background": bool(scope_hint),
                "depth": current_depth(),
                "ts": asyncio.get_running_loop().time(),
            })
        except Exception:
            log("delegate 异常已忽略", "DEBUG")

        try:
            await asyncio.wait_for(semaphore.acquire(), timeout)
        except asyncio.TimeoutError:
            log(f"委托并发槽获取超时（>{timeout:.0f}s）: {goal[:60]}", "WARNING", tag="委托")
            fail_result = SubAgentResult(
                goal=goal, success=False,
                error=f"获取委托并发槽超时（>{timeout:.0f}s），子代理并发已满",
                role=normalize_role(role), task_index=task_index,
            )
            try:
                await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                    "scope": scope, "chat_id": chat_id,
                    "delegation_id": delegation_id, "goal": goal,
                    "success": False, "error": fail_result.error,
                    "task_index": task_index,
                })
            except Exception:
                log("delegate 异常已忽略", "DEBUG")
            return fail_result
        except asyncio.CancelledError:
            # 并发槽等待期间被取消（cancel 先于执行登记到达）
            if delegation_id in self._cancel_marks:
                self._cancel_marks.discard(delegation_id)
                return _cancelled_result(goal, role=role, task_index=task_index)
            raise

        id_token = bind_delegation_id(delegation_id)
        try:
            agent = SubAgent(
                self._mind, goal, context,
                role=role, max_iterations=max_iterations, task_index=task_index,
            )
            run_task = asyncio.create_task(
                agent.run(), name=f"delegation.run.{delegation_id}",
            )
            self._running[delegation_id] = {
                "task": run_task,
                "goal": goal,
                "scope": scope,
                "chat_id": chat_id,
                "role": normalize_role(role),
                "task_index": task_index,
                "background": bool(scope_hint),
                "started_at": time.time(),
            }
            try:
                result = await run_task
            except asyncio.CancelledError:
                # 用户取消：转化为取消结果返回（父级思维循环不受 CancelledError 冲击）；
                # 非用户取消（如服务关闭）继续向上传播
                if delegation_id in self._cancel_marks:
                    log(f"委托已被用户取消: {delegation_id} -> {goal[:60]}", tag="委托")
                    result = _cancelled_result(goal, role=role, task_index=task_index)
                else:
                    raise
            finally:
                self._cancel_marks.discard(delegation_id)
                self._running.pop(delegation_id, None)
        finally:
            reset_delegation_id(id_token)
            semaphore.release()

        # 发射 resolved 事件
        try:
            await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                "scope": scope, "chat_id": chat_id,
                "delegation_id": delegation_id, "goal": goal,
                "success": result.success,
                "output": result.output[:_RESOLVED_OUTPUT_PREVIEW_CHARS],
                "error": result.error,
                "task_index": task_index,
                **({"cancelled": True} if result.cancelled else {}),
            })
        except Exception:
            log("delegate 异常已忽略", "DEBUG")
        return result

    async def delegate_batch(
            self,
            tasks: List[Dict[str, str]],
            *,
            role: str = "leaf",
            max_iterations: int = 0,
    ) -> List[SubAgentResult]:
        """并行委托多个子任务，结果按 task_index 排序。"""
        if len(tasks) > _max_concurrent() * 3:
            raise ValueError(
                f"并行子任务数量超限（{len(tasks)} > {_max_concurrent() * 3}），请拆分批次"
            )
        results = await asyncio.gather(
            *(
                self.delegate(
                    t.get("goal", ""), t.get("context", ""),
                    role=normalize_role(t.get("role") or role),
                    max_iterations=max_iterations,
                    task_index=i,
                )
                for i, t in enumerate(tasks)
            ),
            return_exceptions=True,
        )
        final: List[SubAgentResult] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                final.append(SubAgentResult(
                    goal=tasks[i].get("goal", ""), success=False,
                    error=f"{type(r).__name__}: {r}", task_index=i,
                ))
            else:
                final.append(r)
        final.sort(key=lambda r: r.task_index)
        return final

    # ------------------------------------------------------------------
    # 后台委托
    # ------------------------------------------------------------------

    def delegate_background(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            scope: str = "",
    ) -> str:
        """后台委托：登记注册表后立即返回 delegation_id，结果异步送达。

        送达路径（由 BackgroundTaskRegistry 路由）：
        - 父 Agent 正挂起等待 → 完成事件注入当前思考循环（轮内会合）；
        - 否则 → 完成事件排入回复队列触发新一轮 REPLY（完成即新 turn）。
        """
        registry = getattr(self._mind, "background_tasks", None)
        if registry is not None:
            delegation_id = registry.register(scope or "_global", "delegation", goal[:80])
        else:
            delegation_id = uuid.uuid4().hex[:8]

        # 发射 started 事件
        effective_scope = scope or _current_scope()
        _user_scope, chat_id = _parse_scope_chat_id(effective_scope)
        try:
            asyncio.create_task(event_bus.emit(EVENT_DELEGATION_STARTED, {
                "scope": effective_scope,
                "chat_id": chat_id,
                "delegation_id": delegation_id,
                "goal": goal,
                "context_preview": context[:200],
                "role": normalize_role(role),
                "task_index": 0,
                "background": True,
                "depth": current_depth(),
            }))
        except Exception:
            log("delegate_background 异常已忽略", "DEBUG")

        task = asyncio.create_task(
            self._run_background(delegation_id, goal, context, role, max_iterations, scope),
            name=f"delegation.{delegation_id}",
        )
        self._background_tasks[delegation_id] = task
        task.add_done_callback(lambda _: self._background_tasks.pop(delegation_id, None))
        log(f"后台委托已启动: {delegation_id} -> {goal[:60]}", tag="委托")
        return delegation_id

    async def _run_background(
            self,
            delegation_id: str,
            goal: str,
            context: str,
            role: str,
            max_iterations: int,
            scope: str = "",
    ) -> None:
        """后台执行委托并按注册表路由结果（轮内会合 / 完成即新 turn）。

        异常兜底：delegate 或事件发射的任何异常都必须转化为失败结果并
        完成 registry.complete() 登记，否则 delegation 会永久卡在 running。
        """
        try:
            result = await self.delegate(
                goal, context, role=role, max_iterations=max_iterations,
                scope_hint=scope,
            )
        except asyncio.CancelledError:
            # 用户取消（含并发槽等待阶段）：转化为取消结果继续走正常路由；
            # 非用户取消（服务关闭等）向上传播
            if delegation_id in self._cancel_marks:
                self._cancel_marks.discard(delegation_id)
                result = _cancelled_result(goal, role=role)
            else:
                raise
        except Exception as exc:
            log(f"后台委托执行异常: {delegation_id}: {exc}", "ERROR", tag="委托")
            result = SubAgentResult(
                goal=goal, success=False,
                error=f"后台委托执行异常: {type(exc).__name__}: {exc}",
                role=normalize_role(role),
            )

        status = "已取消" if result.cancelled else ("成功" if result.success else "失败")
        summary = (result.output if result.success else result.error) or ""

        try:
            await event_bus.emit(EVENT_DELEGATION_COMPLETED, {
                "delegation_id": delegation_id,
                "goal": goal,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                **({"cancelled": True} if result.cancelled else {}),
            })

            # 向 webui 前端推 resolved（DelegationCard 关闭/标完成）
            effective_scope = scope or _current_scope()
            _user_scope, chat_id = _parse_scope_chat_id(effective_scope)
            try:
                await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                    "scope": effective_scope,
                    "chat_id": chat_id,
                    "delegation_id": delegation_id,
                    "goal": goal,
                    "success": result.success,
                    "output": result.output[:_RESOLVED_OUTPUT_PREVIEW_CHARS],
                    "error": result.error,
                    "task_index": 0,
                    "background": True,
                    **({"cancelled": True} if result.cancelled else {}),
                })
            except Exception:
                log("_run_background 异常已忽略", "DEBUG")
        except Exception as exc:
            log(f"后台委托事件发射失败: {delegation_id}: {exc}", "WARNING", tag="委托")

        note = (
            f"[后台委托完成] id={delegation_id} 状态={status}\n"
            f"目标: {goal[:200]}\n结果: {summary[:_SUMMARY_NOTICE_MAX_CHARS]}"
        )

        # 结果登记兜底：无论后续路由是否成功，registry 都必须完成，
        # 否则 delegation 永久卡 running、等待者永远收不到会合注入
        registry = getattr(self._mind, "background_tasks", None)
        claimed = False
        try:
            claimed = registry.complete(
                delegation_id, result.success, summary[:_SUMMARY_NOTICE_MAX_CHARS],
            ) if registry else False
        except Exception as exc:
            log(f"后台委托结果登记失败: {delegation_id}: {exc}", "ERROR", tag="委托")

        try:
            if not claimed and not result.cancelled and scope.startswith(("user_", "group_")):
                # 轮外完成（无等待者）：排入回复队列触发新一轮 REPLY，主动汇报结果
                # （用户取消的委托不主动汇报——用户已知悉，写入短期记忆供后续轮次可见即可）
                from agent.mind.tools.scheduler import enqueue_scope_reply
                enqueue_scope_reply(
                    self._mind.pfc,
                    scope,
                    self._mind.pfc.get_adapter_key(scope),
                    f"后台委托完成: {goal[:60]}",
                    note + "\n请将结果告知用户，或根据结果继续未完成的操作。",
                )
                asyncio.create_task(self._mind.try_execute_mind())
            else:
                # 轮内会合（等待者已收到注入）或无回复目标：结果写入短期记忆兜底，
                # 保证后续轮次可见、信息不丢失
                temp_scope = scope if scope.startswith(("user_", "group_")) else ""
                self._mind.pfc.add_temporary({"role": "user", "content": note}, scope=temp_scope)
        except Exception as exc:
            log(f"后台委托结果路由失败: {delegation_id}: {exc}", "ERROR", tag="委托")
        log(f"后台委托完成: {delegation_id} ({status})", tag="委托")

    def background_tasks_snapshot(self, scope: str) -> Dict[str, Any]:
        """当前 scope 后台任务状态快照（check_background_tasks 工具用）。"""
        registry = getattr(self._mind, "background_tasks", None)
        if registry is None:
            return {"running": [], "completed": []}
        return registry.snapshot(scope)

    # ------------------------------------------------------------------
    # 结果聚合
    # ------------------------------------------------------------------

    def aggregate_results(self, results: List[SubAgentResult]) -> str:
        """聚合子代理结果为工具返回（JSON），摘要按父上下文预算截断。"""
        budget = self._summary_char_budget(len(results))
        items: List[Dict[str, Any]] = []
        for r in results:
            output = r.output
            if len(output) > budget:
                output = self._trim_summary(output, budget)
            item: Dict[str, Any] = {
                "task_index": r.task_index,
                "goal": r.goal,
                "success": r.success,
                "output": output,
            }
            if r.error:
                item["error"] = r.error
            if r.cancelled:
                item["cause"] = "user_cancel"
                item["retryable"] = False
            items.append(item)
        succeeded = sum(1 for r in results if r.success)
        return json.dumps({
            "ok": succeeded == len(results),
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": items,
        }, ensure_ascii=False)

    def _summary_char_budget(self, n_summaries: int) -> int:
        """每个子任务摘要的字符预算（父上下文剩余空间均分，参考 hermes）。"""
        context_length = self._mind.get_model_context_length()
        if context_length <= 0:
            return _MAX_SUMMARY_CHARS
        headroom_chars = context_length * _CHARS_PER_TOKEN
        per_summary = int(headroom_chars * _SUMMARY_HEADROOM_FRACTION) // max(1, n_summaries)
        return max(_MIN_SUMMARY_CHARS, min(per_summary, _MAX_SUMMARY_CHARS))

    @staticmethod
    def _trim_summary(text: str, budget: int) -> str:
        """摘要截断：保留头部 75% + 尾部 25% + 截断标记。"""
        head = int(budget * _TRIM_HEAD_FRACTION)
        tail = budget - head
        return (
            f"{text[:head]}\n"
            f"...[摘要过长已截断，原长度={len(text)} 字符]...\n"
            f"{text[-tail:]}"
        )


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_DELEGATION_CONFIGS = {
    "子代理": {
        "delegation_enabled": {
            "description": "是否启用子代理委托",
            "default": True,
        },
        "delegation_max_depth": {
            "description": "最大委托深度（orchestrator 可再委托的层数）",
            "default": 2,
        },
        "delegation_max_concurrent": {
            "description": "子代理并发上限",
            "default": 3,
        },
        "delegation_default_iterations": {
            "description": "子代理默认迭代预算（轮次）",
            "default": 15,
        },
        "delegation_max_iterations_cap": {
            "description": "子代理迭代预算硬上限（轮次）",
            "default": 50,
        },
        "delegation_timeout_seconds": {
            "description": "单个子代理整体执行超时（秒）",
            "default": 600,
        },
        "delegation_acquire_timeout_seconds": {
            "description": "委托并发槽获取超时（秒），超时返回失败而非永久阻塞",
            "default": 300,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_DELEGATION_CONFIGS)
