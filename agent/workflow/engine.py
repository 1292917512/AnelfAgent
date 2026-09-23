"""工作流引擎 — journal 化的 DAG 编排（断点恢复 / 修订导入 / 门控重跑）。

执行面完全复用既有子系统：ask 步走 DelegationManager（并发槽、进度
事件、transcript、取消级联全数继承），tool 步走统一审批门 +
EntityRegistry.execute_tool。引擎只做编排层自己的事：

- **拓扑调度**：graphlib 分层，层内并发（受 workflow_max_parallel_steps
  约束），任一步失败即收敛为 run 失败（fail-fast）。
- **journal 化执行**：准入先落 running 行，终态一笔写；进程崩溃后
  running run 由 recovery 收敛为 stopped(interrupted)。
- **断点恢复**：resume 同 run —— completed 步骤经 input_hash 防御性
  比对后缓存结算（不重付费），running 步骤重派。
- **修订导入**：start(resume_of=父) —— 父 run 每步最新 completed 成果
  在 input_hash 一致时导入为 cached，规格分歧自然级联为重跑。
- **门控重跑**：tool 步的 gate 不满足时先委托子代理按失败上下文修复
  再重跑（有界轮次，每轮独立 ordinal）——「模型生成、代码把关」。

可见性：运行登记 BackgroundTaskRegistry（check_background_tasks 可见、
terminate 可停、完成通知自动路由）；步骤/事件落 journal 供 Web 时间线。

取消语义二分：用户停止（stop_requested 标记）转化为步骤 cancelled
结局、run 结算 stopped(user) 可续跑；进程关停的 CancelledError 原样
上抛，run 留待启动收敛标 interrupted。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.workflow import journal as wf_journal
from agent.workflow.journal import (
    NODE_COMPLETED,
    NODE_FAILED,
    WorkflowJournal,
)
from agent.workflow.spec import (
    WorkflowSpec,
    WorkflowSpecError,
    canonical_spec,
    parse_spec,
    spec_hash,
    topo_layers,
)
from core.config import get_config_bool, get_config_int, register_configs_safe
from core.log import log

_TAG = "工作流"

# 事件/结果字段的截断上限（journal 尺寸纪律）
_INPUT_PREVIEW_CHARS = 600
_RESULT_PREVIEW_CHARS = 2000
_NODE_RESULT_MAX_CHARS = 16_000
_UPSTREAM_CONTEXT_CHARS = 2000
_REPAIR_CONTEXT_CHARS = 4000
# 失败重试的固定退避（秒）
_RETRY_BACKOFF_SECONDS = 1.0


def _hash_input(desc: Dict[str, Any]) -> str:
    """步骤输入指纹（规范化 JSON 的 sha256 前 12 位；缓存比对基础）。"""
    canonical = json.dumps(desc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _bounded(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[截断，原长度 {len(text)}]"


def _dig(data: Any, path: str) -> Any:
    """结果 JSON 的点路径取值（支持 dict 键与 list 下标）。"""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


@dataclass
class _StepOutcome:
    """单步执行结果（status: completed / failed / cancelled）。"""

    status: str
    result: str = ""
    error: str = ""
    cached: bool = False
    delegation_id: str = ""
    usage: Optional[Dict[str, Any]] = None


@dataclass
class _RunState:
    """运行中的内存态（journal 是事实源，这里只放运行时句柄）。"""

    run_id: str
    scope: str
    name: str
    loop: Optional[asyncio.AbstractEventLoop] = None
    task: Optional[asyncio.Task] = None
    registry_id: str = ""
    step_tasks: set = field(default_factory=set)
    # step_key → delegation_id（ask/repair 步；停止级联与续聊数据源）
    delegation_ids: Dict[str, str] = field(default_factory=dict)
    stop_requested: bool = False


class WorkflowEngine:
    """工作流编排引擎（Mind 持有实例；工具/服务层经端口消费）。"""

    def __init__(self, mind: Any) -> None:
        self._mind = mind
        self._journal = WorkflowJournal()
        self._runs: Dict[str, _RunState] = {}
        self._dispatch_sem: Optional[asyncio.Semaphore] = None
        # 引擎构造时刻：启动恢复只收敛此前创建的 run（防恢复晚到误伤新 run）
        self._started_at = time.time()
        # start/resume 的开账-派发段串行锁：并发续跑同一 run 只生效一次
        self._lifecycle_lock = asyncio.Lock()

    @property
    def journal(self) -> WorkflowJournal:
        return self._journal

    def running_ids(self) -> List[str]:
        return list(self._runs)

    def is_running(self, run_id: str) -> bool:
        return run_id in self._runs

    async def aclose(self) -> None:
        await self._journal.aclose()

    # ------------------------------------------------------------------
    # 启动 / 续跑 / 停止
    # ------------------------------------------------------------------

    async def start(self, spec_data: Any, *, scope: str = "", resume_of: str = "") -> Dict[str, Any]:
        """启动工作流（后台执行，立即返回 run 概要）。

        resume_of 非空时为修订运行：父 run 必须已终结，其每步最新
        completed 成果在输入一致时导入（不重付费）。
        """
        if not get_config_bool("workflow_enabled", True):
            raise WorkflowSpecError("工作流引擎已禁用（workflow_enabled）")
        spec = parse_spec(spec_data)
        if resume_of:
            parent = await self._journal.get_run(resume_of)
            if parent is None:
                raise ValueError(f"修订源 run 不存在: {resume_of}")
            if parent["status"] == wf_journal.RUNNING:
                raise ValueError(f"修订源 run 尚未终结: {resume_of}")
        run_id = uuid.uuid4().hex[:12]
        async with self._lifecycle_lock:
            await self._journal.create_run(
                run_id, spec.name, canonical_spec(spec), spec_hash(spec), scope, resume_of,
            )
            self._spawn(run_id, scope, spec.name)
        await self._journal.purge_expired(get_config_int("workflow_retention_days", 30))
        log(f"工作流已启动: {run_id} [{spec.name}] {len(spec.steps)} 步"
            + (f"（修订自 {resume_of}）" if resume_of else ""), tag=_TAG)
        return self._run_summary(await self._journal.get_run(run_id))

    async def resume(self, run_id: str) -> Dict[str, Any]:
        """续跑已停止的 run（completed 步骤缓存结算，中断步骤重派）。"""
        run = await self._journal.get_run(run_id)
        if run is None:
            raise ValueError(f"工作流不存在: {run_id}")
        if run["status"] != wf_journal.STOPPED:
            raise ValueError(f"仅停止的运行可续跑（当前 {run['status']}）；终态运行请修订启动新运行")
        async with self._lifecycle_lock:
            if run_id in self._runs:
                raise ValueError(f"工作流 {run_id} 已在运行中")
            if not await self._journal.reopen_run(run_id):
                raise ValueError(f"续跑开账失败: {run_id}")
            self._spawn(run_id, str(run["scope"]), str(run["name"]))
        log(f"工作流续跑: {run_id} [{run['name']}]", tag=_TAG)
        return self._run_summary(await self._journal.get_run(run_id))

    def stop(self, run_id: str, reason: str = "user") -> Dict[str, Any]:
        """请求停止（线程安全）：标记先行，取消经运行循环线程侧执行。

        run 终态由执行循环结算（stopped(user)），步骤的取消结局也由
        循环收敛——stop 只发信号不自造终态。
        """
        state = self._runs.get(run_id)
        if state is None:
            return {"ok": False, "error": f"工作流不在运行中: {run_id}"}
        state.stop_requested = True
        loop = state.loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._stop_in_loop, state)
            except RuntimeError:
                pass  # 循环已关停（进程退出中）
        return {"ok": True, "note": "停止信号已发出，结束后可用续跑恢复"}

    def _stop_in_loop(self, state: _RunState) -> None:
        """循环线程内执行取消：ask 走 manager.cancel（转用户取消结果），其余任务取消。"""
        manager = getattr(self._mind, "delegation_manager", None)
        if manager is not None:
            for did in list(state.delegation_ids.values()):
                try:
                    manager.cancel(did)
                except Exception:
                    pass
        for task in list(state.step_tasks):
            if not task.done():
                task.cancel()

    def _spawn(self, run_id: str, scope: str, name: str) -> None:
        state = _RunState(run_id=run_id, scope=scope, name=name,
                          loop=asyncio.get_running_loop())
        registry = getattr(self._mind, "background_tasks", None)
        if registry is not None:
            state.registry_id = registry.register(scope, "workflow", f"[{name}] 工作流运行")
            engine = self

            def _kill() -> bool:
                return bool(engine.stop(run_id).get("ok"))

            registry.attach_killer(state.registry_id, _kill)
        self._runs[run_id] = state
        state.task = asyncio.create_task(self._execute_run(run_id), name=f"workflow.{run_id}")

        def _done(task: asyncio.Task) -> None:
            self._runs.pop(run_id, None)
            if not task.cancelled():
                _ = task.exception()

        state.task.add_done_callback(_done)

    # ------------------------------------------------------------------
    # 执行循环
    # ------------------------------------------------------------------

    async def _execute_run(self, run_id: str) -> None:
        state = self._runs.get(run_id)
        run = await self._journal.get_run(run_id)
        if state is None or run is None:
            return
        spec = WorkflowSpec.model_validate(json.loads(run["spec_json"]))
        by_key = {s.key: s for s in spec.steps}
        try:
            await self._journal.append_event(run_id, "run-started", {
                "name": spec.name, "spec_hash": run["spec_hash"],
                "parent_run_id": run.get("parent_run_id") or "",
            })
            existing = await self._journal.latest_nodes(run_id)
            imports: Dict[str, Dict[str, Any]] = {}
            if run.get("parent_run_id"):
                imports = await self._journal.importable_nodes(str(run["parent_run_id"]))

            results: Dict[str, str] = {}
            failure: Optional[Dict[str, Any]] = None
            stopped = False
            for layer in topo_layers(spec):
                if state.stop_requested:
                    stopped = True
                    break
                step_tasks = [
                    asyncio.create_task(
                        self._run_step(run_id, run, by_key[key], state, existing,
                                       imports, results),
                        name=f"workflow.{run_id}.{key}",
                    )
                    for key in layer
                ]
                state.step_tasks.update(step_tasks)
                try:
                    outcomes = await asyncio.gather(*step_tasks)
                finally:
                    state.step_tasks.difference_update(step_tasks)
                if state.stop_requested or any(oc.status == "cancelled" for oc in outcomes):
                    stopped = True
                    break
                failed_keys = [key for key, oc in zip(layer, outcomes, strict=True) if oc.status == NODE_FAILED]
                if failed_keys:
                    failure = {
                        "message": "步骤失败，工作流终止（可修订规格后以 resume_of 重启导入已完成步骤）",
                        "failed_steps": [
                            {"key": key,
                             "error": _bounded(oc.error, 2000)}
                            for key, oc in zip(layer, outcomes, strict=True) if oc.status == NODE_FAILED
                        ],
                    }
                    break

            if stopped:
                await self._journal.settle_run(
                    run_id, wf_journal.STOPPED, stop_reason="user",
                    failure={"message": "被停止（已完成步骤保留，可续跑）"},
                )
                await self._journal.append_event(
                    run_id, "run-settled", {"status": wf_journal.STOPPED, "stop_reason": "user"})
                self._complete_registry(state, False, f"工作流 [{spec.name}] 已停止（可续跑）")
            elif failure is not None:
                await self._journal.settle_run(run_id, wf_journal.FAILED, failure=failure)
                await self._journal.append_event(run_id, "run-settled", {
                    "status": wf_journal.FAILED,
                    "failed_steps": [f["key"] for f in failure["failed_steps"]]})
                self._complete_registry(state, False, f"工作流 [{spec.name}] 失败: "
                                                       f"{', '.join(f['key'] for f in failure['failed_steps'])}")
            else:
                result = {
                    "name": spec.name,
                    "steps": {
                        key: {"status": NODE_COMPLETED,
                              "result": _bounded(results.get(key, ""), _RESULT_PREVIEW_CHARS)}
                        for key in by_key
                    },
                }
                await self._journal.settle_run(run_id, wf_journal.COMPLETED, result=result)
                await self._journal.append_event(
                    run_id, "run-settled", {"status": wf_journal.COMPLETED, "steps": len(by_key)})
                self._complete_registry(state, True, f"工作流 [{spec.name}] 完成（{len(by_key)} 步）")
            log(f"工作流结算: {run_id} [{spec.name}]", tag=_TAG)
        except asyncio.CancelledError:
            # 进程关停级取消：run 留在 running，由启动收敛标 interrupted
            raise
        except Exception as exc:
            log(f"工作流引擎异常: {run_id}: {exc}", "ERROR", tag=_TAG)
            await self._journal.settle_run(
                run_id, wf_journal.FAILED,
                failure={"message": f"引擎异常: {type(exc).__name__}: {exc}"})
            await self._journal.append_event(run_id, "run-settled", {
                "status": wf_journal.FAILED, "error": str(exc)[:500]})
            self._complete_registry(state, False, f"工作流 [{spec.name}] 引擎异常")

    def _complete_registry(self, state: _RunState, success: bool, summary: str) -> None:
        registry = getattr(self._mind, "background_tasks", None)
        if registry is not None and state.registry_id:
            try:
                registry.complete(state.registry_id, success, summary, claimed=False)
            except Exception as exc:
                log(f"工作流完成通知失败: {exc}", "DEBUG", tag=_TAG)

    # ------------------------------------------------------------------
    # 单步执行（重放 / 导入 / 派发 / 门控）
    # ------------------------------------------------------------------

    async def _run_step(
            self, run_id: str, run: Dict[str, Any], step: Any,
            state: _RunState, existing: Dict[str, Dict[str, Any]],
            imports: Dict[str, Dict[str, Any]], results: Dict[str, str],
    ) -> _StepOutcome:
        try:
            return await self._run_step_inner(run_id, run, step, state, existing, imports, results)
        except asyncio.CancelledError:
            if state.stop_requested:
                return _StepOutcome(status="cancelled", error="停止请求")
            raise  # 进程关停：上抛由执行循环留待启动收敛

    async def _run_step_inner(
            self, run_id: str, run: Dict[str, Any], step: Any,
            state: _RunState, existing: Dict[str, Dict[str, Any]],
            imports: Dict[str, Dict[str, Any]], results: Dict[str, str],
    ) -> _StepOutcome:
        context = ""
        if step.kind == "ask":
            context = self._upstream_context(step, results)
            input_desc: Dict[str, Any] = {
                "goal": step.goal, "context": context,
                "agent": step.agent_name, "continue_from": step.continue_from,
            }
        else:
            input_desc = {"tool": step.tool, "args": step.args}
        input_hash = _hash_input(input_desc)
        input_json = _bounded(json.dumps(input_desc, ensure_ascii=False), _INPUT_PREVIEW_CHARS)

        def _gate_passes(result_text: str) -> bool:
            """缓存结算前的门控复验：门不过的成果不可当完成（继续修复+重跑）。"""
            if step.kind != "tool" or step.gate is None:
                return True
            ok, _actual = self._gate_check(result_text, step.gate)
            return ok

        # 同 run 重放（resume）：completed 缓存结算 / failed 复现 / running 重派
        prior = existing.get(step.key)
        if prior is not None:
            if prior["input_hash"] != input_hash:
                error = (f"重放偏移：步骤 {step.key} 的 journal 记录与规格推导不一致"
                         f"（{prior['input_hash']} != {input_hash}），拒绝缓存结算")
                log(error, "ERROR", tag=_TAG)
                return _StepOutcome(status=NODE_FAILED, error=error)
            if prior["status"] == NODE_COMPLETED:
                cached_result = str(prior.get("result_text") or "")
                if _gate_passes(cached_result):
                    results[step.key] = cached_result
                    if prior.get("delegation_id"):
                        state.delegation_ids[step.key] = str(prior["delegation_id"])
                    await self._emit_settled(run_id, step.key, prior["ordinal"],
                                             NODE_COMPLETED, cached=True)
                    return _StepOutcome(status=NODE_COMPLETED, result=cached_result, cached=True)
                # 门控未过的历史轮：不当完成，换新 ordinal 进修复+重跑循环
            elif prior["status"] == NODE_FAILED:
                return _StepOutcome(status=NODE_FAILED,
                                    error=str(prior.get("error_text") or "历史失败（重放）"))

        # 修订导入：父 run 同名步骤输入一致且门控可通过 → 成果直接入账（不重付费）
        imported = imports.get(step.key)
        if imported is not None and imported["input_hash"] == input_hash \
                and _gate_passes(str(imported.get("result_text") or "")):
            ordinal = await self._journal.next_ordinal(run_id, step.key)
            await self._journal.admit_node(run_id, step.key, ordinal, step.kind,
                                           input_hash, input_json)
            await self._journal.settle_node(
                run_id, step.key, ordinal,
                result_text=str(imported.get("result_text") or ""),
                delegation_id=str(imported.get("delegation_id") or ""),
                usage=self._usage_of(imported),
            )
            imported_result = str(imported.get("result_text") or "")
            results[step.key] = imported_result
            if imported.get("delegation_id"):
                state.delegation_ids[step.key] = str(imported["delegation_id"])
            await self._emit_settled(run_id, step.key, ordinal, NODE_COMPLETED, cached=True)
            return _StepOutcome(status=NODE_COMPLETED, result=imported_result, cached=True)

        ordinal = int(prior["ordinal"]) \
            if prior is not None and prior["status"] == wf_journal.NODE_RUNNING \
            else await self._journal.next_ordinal(run_id, step.key)

        gate_cfg = step.gate if step.kind == "tool" else None
        # 执行预算：门控轮次（tool）或单次，加上通用失败重试预算
        max_attempts = (int(gate_cfg.max_rounds) if gate_cfg is not None else 1) \
            + int(step.retries)
        retries_left = int(step.retries)
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            if state.stop_requested:
                return _StepOutcome(status="cancelled", error="停止请求")
            await self._journal.admit_node(run_id, step.key, ordinal, step.kind,
                                           input_hash, input_json)
            await self._journal.append_event(run_id, "node-admitted", {
                "key": step.key, "ordinal": ordinal, "kind": step.kind,
                "phase": step.phase or "",
                "input_preview": _bounded(
                    step.goal or f"{step.tool}({json.dumps(step.args, ensure_ascii=False)})",
                    200),
            })
            outcome = await self._dispatch(run, step, state, existing, context)
            if outcome.status == "cancelled":
                # 节点行保持 running：续跑时按「执行中被打断」重派本 ordinal
                # （节点终态只有 completed/failed，取消不是步骤结局而是运行暂停）
                await self._emit_settled(run_id, step.key, ordinal, "cancelled",
                                         error=_bounded(outcome.error, 400))
                return outcome
            await self._journal.settle_node(
                run_id, step.key, ordinal,
                result_text=_bounded(outcome.result, _NODE_RESULT_MAX_CHARS) if outcome.result else "",
                error_text=_bounded(outcome.error, 4000) if outcome.error else "",
                delegation_id=outcome.delegation_id, usage=outcome.usage,
            )
            await self._emit_settled(run_id, step.key, ordinal, outcome.status,
                                     error=_bounded(outcome.error, 400))

            if outcome.status == NODE_COMPLETED and gate_cfg is not None:
                ok, actual = self._gate_check(outcome.result, gate_cfg)
                if ok:
                    results[step.key] = outcome.result
                    return _StepOutcome(status=NODE_COMPLETED, result=outcome.result,
                                        delegation_id=outcome.delegation_id)
                if attempt < max_attempts:
                    repair = await self._run_repair(run_id, step, state,
                                                    gate_cfg, actual, outcome.result)
                    if repair.status == "cancelled":
                        return repair
                    if repair.status != NODE_COMPLETED:
                        return _StepOutcome(
                            status=NODE_FAILED,
                            error=f"门控未通过且修复委托失败: {repair.error}")
                    ordinal = await self._journal.next_ordinal(run_id, step.key)
                    continue
                return _StepOutcome(
                    status=NODE_FAILED,
                    error=_bounded(
                        f"门控未通过：{gate_cfg.field} 期望 {gate_cfg.equals!r}，"
                        f"实际 {actual!r}；末轮结果：{outcome.result}", 2000),
                )

            if outcome.status == NODE_COMPLETED:
                results[step.key] = outcome.result
                return outcome
            if outcome.status == "cancelled":
                return outcome

            # 失败：重试预算内换 ordinal 再跑，否则本步失败
            if retries_left > 0:
                retries_left -= 1
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                ordinal = await self._journal.next_ordinal(run_id, step.key)
                continue
            return outcome
        return _StepOutcome(status=NODE_FAILED, error="执行预算耗尽")

    def _gate_check(self, result_text: str, gate: Any) -> Tuple[bool, Any]:
        """门控判定：结果 JSON 的点路径取值与期望比较（解析失败即不通过）。"""
        try:
            parsed = json.loads(result_text)
        except ValueError:
            return False, "<非 JSON 结果>"
        actual = _dig(parsed, gate.field)
        return actual == gate.equals, actual

    async def _run_repair(
            self, run_id: str, step: Any, state: _RunState,
            gate: Any, actual: Any, last_result: str,
    ) -> _StepOutcome:
        """门控失败后的修复委托（独立 repair 节点行，成功后主步骤重跑）。"""
        goal = (gate.repair_goal or "").strip() or (
            f"工具步骤 {step.key} 的执行结果未通过门控"
            f"（{gate.field} 应为 {gate.equals!r}，实际 {actual!r}）。"
            "请分析失败原因并修复，使下次执行能通过门控。")
        context = _bounded(last_result, _REPAIR_CONTEXT_CHARS)
        key = f"{step.key}#repair"
        input_hash = _hash_input({"goal": goal, "context": context, "repair_for": step.key})
        ordinal = await self._journal.next_ordinal(run_id, key)
        await self._journal.admit_node(run_id, key, ordinal, "repair", input_hash,
                                       _bounded(goal, _INPUT_PREVIEW_CHARS))
        await self._journal.append_event(run_id, "node-admitted", {
            "key": key, "ordinal": ordinal, "kind": "repair", "phase": step.phase or "",
            "input_preview": _bounded(goal, 200),
        })
        outcome = await self._dispatch_ask(goal, context, "", state, key)
        await self._journal.settle_node(
            run_id, key, ordinal,
            result_text=_bounded(outcome.result, _NODE_RESULT_MAX_CHARS) if outcome.result else "",
            error_text=_bounded(outcome.error, 4000)
            if outcome.error or outcome.status != NODE_COMPLETED else "",
            delegation_id=outcome.delegation_id, usage=outcome.usage,
        )
        await self._emit_settled(run_id, key, ordinal,
                                 NODE_COMPLETED if outcome.status == NODE_COMPLETED else NODE_FAILED,
                                 error=_bounded(outcome.error, 400))
        return outcome

    # ------------------------------------------------------------------
    # 派发（ask → DelegationManager；tool → 审批 + EntityRegistry）
    # ------------------------------------------------------------------

    async def _dispatch(
            self, run: Dict[str, Any], step: Any, state: _RunState,
            existing: Dict[str, Dict[str, Any]], context: str,
    ) -> _StepOutcome:
        if step.kind == "ask":
            return await self._dispatch_ask(step.goal, context, step.agent_name,
                                            state, step.key, step.continue_from, existing,
                                            timeout=float(step.timeout or 0))
        return await self._dispatch_tool(run, step, timeout=float(step.timeout or 0))

    async def _dispatch_ask(
            self, goal: str, context: str, agent_name: str,
            state: _RunState, node_key: str, continue_from: str = "",
            existing: Optional[Dict[str, Dict[str, Any]]] = None,
            timeout: float = 0.0,
    ) -> _StepOutcome:
        manager = getattr(self._mind, "delegation_manager", None)
        if manager is None:
            return _StepOutcome(status=NODE_FAILED, error="委托管理器未初始化")
        registry = getattr(self._mind, "background_tasks", None)
        delegation_id = ""
        if registry is not None:
            delegation_id = registry.register(
                state.scope, "delegation", f"[{state.name}] {goal}"[:80])

        base_messages = self._continuation_messages(state, existing or {}, continue_from, goal)
        result: Optional[Any] = None
        try:
            coro = manager.delegate(
                goal, context, agent_name=agent_name, scope_hint=state.scope,
                delegation_id=delegation_id, base_messages=base_messages,
            )
            if self._dispatch_sem is None:
                self._dispatch_sem = asyncio.Semaphore(
                    max(1, get_config_int("workflow_max_parallel_steps", 3)))
            async with self._dispatch_sem:
                result = await (asyncio.wait_for(coro, timeout) if timeout > 0 else coro)
        except asyncio.TimeoutError:
            return _StepOutcome(status=NODE_FAILED,
                                error=f"步骤超时（{timeout:.0f}s）: {goal[:120]}")
        finally:
            if delegation_id and registry is not None:
                preview = ""
                if result is not None:
                    preview = ((result.output if result.success else result.error) or "")[:1500]
                try:
                    registry.complete(delegation_id, bool(result and result.success),
                                      preview, claimed=True)
                except Exception:
                    pass  # 注册表收尾失败不影响步骤结局

        if result.cancelled:
            return _StepOutcome(status="cancelled", error=result.error or "已取消",
                                delegation_id=delegation_id)
        if not result.success:
            return _StepOutcome(status=NODE_FAILED, error=result.error or "子代理执行失败",
                                delegation_id=delegation_id)
        state.delegation_ids[node_key] = delegation_id
        usage = dict(getattr(result, "usage", {}) or {})
        return _StepOutcome(status=NODE_COMPLETED, result=result.output,
                            delegation_id=delegation_id, usage=usage or None)

    def _continuation_messages(
            self, state: _RunState, existing: Dict[str, Dict[str, Any]],
            continue_from: str, goal: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """continue_from 的续聊消息链：以源步骤 transcript 为 base_messages。"""
        if not continue_from:
            return None
        delegation_id = state.delegation_ids.get(continue_from) or \
            str((existing.get(continue_from) or {}).get("delegation_id") or "")
        if not delegation_id:
            log(f"continue_from 缺少 transcript 源，回退全新委托: {continue_from}", "WARNING", tag=_TAG)
            return None
        from agent.delegation import journal as delegation_journal
        transcript = delegation_journal.load_transcript(delegation_id)
        if transcript is None:
            log(f"continue_from transcript 不可用（过期/超限），回退全新委托: {continue_from}",
                "WARNING", tag=_TAG)
            return None
        messages = list(transcript.get("messages") or [])[-400:]
        messages.append({
            "role": "user",
            "content": f"[工作流续聊] 以上是步骤 {continue_from} 的完整执行上下文，"
                       f"请在既有进展基础上继续完成：\n{goal}",
            "_source": {"origin": "steer"},
        })
        return messages

    async def _dispatch_tool(
            self, run: Dict[str, Any], step: Any, timeout: float = 0.0,
    ) -> _StepOutcome:
        decision = await self._request_approval(step, run)
        from agent.approval.session import ApprovalDecision
        if decision is not ApprovalDecision.APPROVED:
            return _StepOutcome(status=NODE_FAILED,
                                error=f"审批未通过（{getattr(decision, 'value', decision)}）")
        from core.entity import EntityRegistry
        arguments = json.dumps(step.args, ensure_ascii=False)
        coro = EntityRegistry.execute_tool(step.tool, arguments)
        try:
            result_str = await (asyncio.wait_for(coro, timeout) if timeout > 0 else coro)
        except asyncio.TimeoutError:
            return _StepOutcome(status=NODE_FAILED,
                                error=f"步骤超时（{timeout:.0f}s）: {step.tool}")
        parsed = self._parse_tool_result(result_str)
        if isinstance(parsed, dict) and parsed.get("error"):
            return _StepOutcome(status=NODE_FAILED,
                                error=str(parsed["error"])[:4000], result=result_str)
        return _StepOutcome(status=NODE_COMPLETED, result=result_str)

    async def _request_approval(self, step: Any, run: Dict[str, Any]) -> Any:
        """工具步审批（无人可问路径：规则引擎 + Guardian 裁决，与子代理路径同语义）。"""
        from agent.approval.gate import get_approval_gate
        from agent.approval.session import ApprovalDecision
        try:
            return await get_approval_gate().request_approval(
                tool_name=step.tool, tool_args=dict(step.args),
                reason=f"工作流[{run['name']}] 步骤 {step.key}",
                channel=None, chat_id=str(run.get("scope") or ""), user_id="workflow",
            )
        except Exception as exc:
            log(f"工作流审批门异常（按放行处理）: {exc}", "WARNING", tag=_TAG)
            return ApprovalDecision.APPROVED

    @staticmethod
    def _parse_tool_result(result_str: str) -> Any:
        try:
            return json.loads(result_str)
        except ValueError:
            return None

    @staticmethod
    def _usage_of(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raw = node.get("usage_json")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except ValueError:
            return None

    def _upstream_context(self, step: Any, results: Dict[str, str]) -> str:
        """依赖步骤结果的有界注入（每步 ≤ workflow_upstream_context_chars）。"""
        bound = max(200, get_config_int("workflow_upstream_context_chars",
                                        _UPSTREAM_CONTEXT_CHARS))
        blocks = []
        for dep in step.depends_on:
            if dep in results:
                blocks.append(f"[上游步骤 {dep} 结果]\n{_bounded(results[dep], bound)}")
        return "\n\n".join(blocks)

    async def _emit_settled(self, run_id: str, key: str, ordinal: int,
                            outcome: str, *, cached: bool = False,
                            error: str = "") -> None:
        await self._journal.append_event(run_id, "node-settled", {
            "key": key, "ordinal": ordinal, "outcome": outcome,
            "cached": cached, "error": error,
        })

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        runs = await self._journal.list_runs(limit)
        for item in runs:
            item["running"] = item["id"] in self._runs
        return runs

    async def run_detail(self, run_id: str) -> Dict[str, Any]:
        run = await self._journal.get_run(run_id)
        if run is None:
            raise ValueError(f"工作流不存在: {run_id}")
        nodes = await self._journal.nodes_of_run(run_id)
        events = await self._journal.list_events(run_id, limit=400)
        return {
            "run": self._run_summary(run),
            "nodes": [
                {
                    "key": n["step_key"], "ordinal": n["ordinal"], "kind": n["kind"],
                    "status": n["status"],
                    "result_preview": _bounded(str(n.get("result_text") or ""), 400),
                    "error": _bounded(str(n.get("error_text") or ""), 400),
                    "delegation_id": n.get("delegation_id") or "",
                    "created_at": n["created_at"], "updated_at": n["updated_at"],
                }
                for n in nodes
            ],
            "events": events,
        }

    @staticmethod
    def _run_summary(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if run is None:
            return {}
        return {
            "run_id": run["id"], "name": run["name"], "status": run["status"],
            "stop_reason": run.get("stop_reason") or "",
            "scope": run.get("scope") or "",
            "parent_run_id": run.get("parent_run_id") or "",
            "spec_hash": run.get("spec_hash") or "",
            "created_at": run.get("created_at"),
            "finished_at": run.get("finished_at"),
        }


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_WORKFLOW_CONFIGS = {
    "workflow/core": {
        "workflow_enabled": {
            "description": "是否启用工作流引擎",
            "default": True,
        },
        "workflow_max_parallel_steps": {
            "description": "工作流步骤并发上限（ask 委托的引擎侧闸门）",
            "default": 3,
            "advanced": True,
            "unit": "个",
        },
        "workflow_upstream_context_chars": {
            "description": "上游步骤结果注入下游 context 的单步字符上限",
            "default": 2000,
            "advanced": True,
            "unit": "字符",
        },
        "workflow_retention_days": {
            "description": "终态工作流运行记录保留天数",
            "default": 30,
            "advanced": True,
            "unit": "天",
        },
    },
}

register_configs_safe(_WORKFLOW_CONFIGS)
