"""工作流引擎 — journal 化的 DAG 编排（断点恢复 / 修订导入 / 门控重跑）。

执行面完全复用既有子系统：ask 步走 DelegationManager（并发槽、进度
事件、transcript、取消级联全数继承），tool 步走统一审批门 +
EntityRegistry.execute_tool。引擎只做编排层自己的事：

- **拓扑调度**：graphlib 分层，层内并发（受 workflow_max_parallel_steps
  约束），任一步失败即收敛为 run 失败（fail-fast）。
- **journal 化执行**：准入先落 running 行，终态一笔写；进程崩溃后
  running run 由 recovery 收敛为 stopped(interrupted)。
- **断点恢复**：resume 同 run —— completed 步骤经 input_hash 防御性
  比对后缓存结算（不重付费）；工具按派发事实与显式重放策略恢复。
- **修订导入**：start(resume_of=父) —— 父 run 每步最新尝试的 completed 成果
  在 input_hash 一致时导入为 cached，规格分歧自然级联为重跑。
- **门控重跑**：tool 步的 gate 不满足时先委托子代理按失败上下文修复
  再重跑（有界轮次，每轮独立 ordinal）——「模型生成、代码把关」。

可见性：运行登记 BackgroundTaskRegistry（check_background_tasks 可见、
terminate 可停、完成通知自动路由）；步骤/事件落 journal 供 Web 时间线。

取消语义二分：用户停止（stop_requested 标记）转化为步骤 cancelled
结局、run 结算 stopped(user) 可续跑；进程关停的 CancelledError 原样
上抛，run 留待启动收敛标 interrupted。

Model Experience：模型收到步骤背景与依赖证据；工具展示回执只认可实际落账的数据。
Token：步骤背景按需进入本次委托；内部展示回执不增加内容。
缓存：背景属于步骤输入指纹与动态指令，不改变全局稳定前缀。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.approval.types import ApprovalResult
from agent.workflow import journal as wf_journal
from agent.workflow.contracts import bind_input, check_gate, content_digest, input_digest, receipt_origin, step_input
from agent.workflow.journal import (
    NODE_COMPLETED,
    NODE_FAILED,
    WorkflowJournal,
)
from agent.workflow.spec import (
    GateSpec,
    WorkflowSpec,
    WorkflowSpecError,
    WorkflowStepSpec,
    canonical_spec,
    parse_spec,
    spec_hash,
    topo_layers,
)
from core.config import get_config_bool, get_config_int, register_configs_safe
from core.log import log
from core.sanitizer import sanitize_text
from core.tool_results import PresentedToolResult, extract_error_text, parse_tool_result_json, redact_tool_result

if TYPE_CHECKING:
    from agent.delegation.delegation_manager import DelegationManager
    from agent.mind.background.tasks import BackgroundTaskRegistry

_TAG = "工作流"

# 模型背景与展示预览的上限；完整执行回执不按预览长度截断。
_INPUT_PREVIEW_CHARS = 600
_RESULT_PREVIEW_CHARS = 2000
_NODE_RESULT_PREVIEW_CHARS = 16_000
_UPSTREAM_CONTEXT_CHARS = 2000
_REPAIR_CONTEXT_CHARS = 4000
# 失败重试的固定退避（秒）
_RETRY_BACKOFF_SECONDS = 1.0


def _bounded(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[截断，原长度 {len(text)}]"


@dataclass
class _StepOutcome:
    """单步执行结果（status: completed / failed / cancelled）。"""

    status: str
    result: str = ""
    error: str = ""
    cached: bool = False
    delegation_id: str = ""
    usage: Optional[Dict[str, Any]] = None
    retryable: bool = True
    outcome_unknown: bool = False
    sensitive_result: bool = False
    result_origin: str = ""


@dataclass
class _RunState:
    """运行句柄与本轮证据投影；journal 是持久事实源。"""

    run_id: str
    scope: str
    name: str
    loop: Optional[asyncio.AbstractEventLoop] = None
    task: asyncio.Task[None] | None = None
    registry_id: str = ""
    step_tasks: set[asyncio.Task[_StepOutcome]] = field(default_factory=set)
    # step_key → delegation_id（ask/repair 步；停止级联与续聊数据源）
    delegation_ids: Dict[str, str] = field(default_factory=dict)
    stop_requested: bool = False
    input_hashes: dict[str, str] = field(default_factory=dict)
    result_views: dict[str, str] = field(default_factory=dict)
    result_origins: dict[str, str] = field(default_factory=dict)
    parent_steps: dict[str, WorkflowStepSpec] = field(default_factory=dict)

    def stopping(self) -> bool:
        """读取可被取消入口更新的停止信号。"""
        return self.stop_requested


class WorkflowEngine:
    """工作流编排引擎，工具与服务层经端口消费。"""

    def __init__(self, background_tasks: BackgroundTaskRegistry | None, delegation_manager: DelegationManager | None) -> None:
        self._background_tasks = background_tasks
        self._delegation_manager = delegation_manager
        self._journal = WorkflowJournal()
        self._runs: Dict[str, _RunState] = {}
        self._dispatch_sem: Optional[asyncio.Semaphore] = None
        # 引擎构造时刻：启动恢复只收敛此前创建的 run（防恢复晚到误伤新 run）
        self._started_at = time.time()
        # start/resume 的开账-派发段串行锁：并发续跑同一 run 只生效一次
        self._lifecycle_lock = asyncio.Lock()
        self._closing = False

    @property
    def journal(self) -> WorkflowJournal:
        return self._journal

    def running_ids(self) -> List[str]:
        return list(self._runs)

    def is_running(self, run_id: str) -> bool:
        return run_id in self._runs

    async def aclose(self) -> None:
        """停止接收运行，等待在途步骤取消收敛后关闭 journal。"""
        async with self._lifecycle_lock:
            self._closing = True
            tasks = {state.task for state in self._runs.values() if state.task is not None}
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._journal.aclose()

    # ------------------------------------------------------------------
    # 启动 / 续跑 / 停止
    # ------------------------------------------------------------------

    async def start(self, spec_data: Any = None, *, scope: str = "", resume_of: str = "",
                    workflow_id: str = "") -> Dict[str, Any]:
        """启动工作流（后台执行，立即返回 run 概要）。

        resume_of 非空时为修订运行：父 run 必须已终结，其每步最新
        completed 成果在输入一致时导入（不重付费）。
        """
        if not get_config_bool("workflow_enabled", True):
            raise WorkflowSpecError("工作流引擎已禁用（workflow_enabled）")
        if (spec_data is None) == (not workflow_id):
            raise ValueError("spec 与 workflow_id 必须且只能提供一个")
        run_id = uuid.uuid4().hex[:12]
        async with self._lifecycle_lock:
            if self._closing:
                raise RuntimeError("工作流引擎正在关闭")
            if workflow_id:
                definition = await self._journal.get_definition(workflow_id)
                spec_data = definition["spec"]
            spec = parse_spec(spec_data)
            if resume_of:
                parent = await self._journal.get_run(resume_of)
                if parent is None:
                    raise ValueError(f"修订源 run 不存在: {resume_of}")
                if parent["status"] == wf_journal.RUNNING or resume_of in self._runs:
                    raise ValueError(f"修订源 run 尚未终结: {resume_of}")
            await self._journal.create_run(
                run_id, spec.name, canonical_spec(spec), spec_hash(spec), scope, resume_of,
            )
            self._spawn(run_id, scope, spec.name)
            await self._journal.purge_expired(
                get_config_int("workflow_retention_days", 30), protected_ids=self.running_ids())
            summary = self._run_summary(await self._journal.get_run(run_id))
        log(f"工作流已启动: {run_id} [{spec.name}] {len(spec.steps)} 步"
            + (f"（修订自 {resume_of}）" if resume_of else ""), tag=_TAG)
        return summary

    async def resume(self, run_id: str) -> Dict[str, Any]:
        """续跑已停止的 run；已派发工具缺少回执时先检查显式重放契约。"""
        async with self._lifecycle_lock:
            if self._closing:
                raise RuntimeError("工作流引擎正在关闭")
            if run_id in self._runs:
                raise ValueError(f"工作流 {run_id} 已在运行中")
            run = await self._journal.get_run(run_id)
            if run is None:
                raise ValueError(f"工作流不存在: {run_id}")
            if run["status"] != wf_journal.STOPPED:
                raise ValueError(f"仅停止的运行可续跑（当前 {run['status']}）；终态运行请修订启动新运行")
            if not await self._journal.reopen_run(run_id):
                raise ValueError(f"续跑开账失败: {run_id}")
            self._spawn(run_id, str(run["scope"]), str(run["name"]))
            summary = self._run_summary(await self._journal.get_run(run_id))
        log(f"工作流续跑: {run_id} [{run['name']}]", tag=_TAG)
        return summary

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
        manager = self._delegation_manager
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
        registry = self._background_tasks
        if registry is not None:
            state.registry_id = registry.register(scope, "workflow", f"[{name}] 工作流运行")
            engine = self

            def _kill() -> bool:
                return bool(engine.stop(run_id).get("ok"))

            registry.attach_killer(state.registry_id, _kill)
        self._runs[run_id] = state
        state.task = asyncio.create_task(self._execute_run(run_id), name=f"workflow.{run_id}")

        def _done(task: asyncio.Task[None]) -> None:
            self._runs.pop(run_id, None)
            if not task.cancelled():
                _ = task.exception()

        state.task.add_done_callback(_done)

    # ------------------------------------------------------------------
    # 执行循环
    # ------------------------------------------------------------------

    async def _execute_run(self, run_id: str) -> None:
        from agent.skills.execution import skill_execution
        with skill_execution():
            await self._execute_run_body(run_id)

    async def _execute_run_body(self, run_id: str) -> None:
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
                parent_id = str(run["parent_run_id"])
                imports = await self._journal.latest_nodes(parent_id)
                parent = await self._journal.get_run(parent_id)
                if parent is None:
                    raise ValueError(f"修订源 run 不存在: {parent_id}")
                parent_spec = WorkflowSpec.model_validate_json(parent["spec_json"])
                state.parent_steps = {step.key: step for step in parent_spec.steps}
                self._bind_legacy_receipts(parent_spec, imports, is_revision=bool(parent.get("parent_run_id")))

            results: Dict[str, str] = {}
            failure: Optional[Dict[str, Any]] = None
            stopped = False
            for layer in topo_layers(spec):
                if state.stopping():
                    stopped = True
                    break
                outcomes: list[_StepOutcome] = []
                # 明确声明安全的工具与独立 ask 可并行，其余工具在层内形成顺序边界。
                groups: list[list[str]] = []
                parallel = False
                from core.entity import EntityRegistry
                for key in layer:
                    node = by_key[key]
                    entity = EntityRegistry.get(node.tool) if node.kind == "tool" else None
                    safe = node.kind == "ask" or bool(entity and entity.meta.get("concurrency_safe"))
                    if safe and parallel:
                        groups[-1].append(key)
                    else:
                        groups.append([key])
                    parallel = safe
                for group in groups:
                    step_tasks = []
                    try:
                        try:
                            async with asyncio.TaskGroup() as nursery:
                                for key in group:
                                    task = nursery.create_task(
                                        self._run_step(run_id, run, by_key[key], state, existing, imports, results),
                                        name=f"workflow.{run_id}.{key}",
                                    )
                                    step_tasks.append(task)
                                    state.step_tasks.add(task)
                        except ExceptionGroup as exc:
                            raise exc.exceptions[0] from exc
                        outcomes.extend(task.result() for task in step_tasks)
                    finally:
                        state.step_tasks.difference_update(step_tasks)
                    if state.stopping():
                        break
                    if any(outcome.status == NODE_FAILED for outcome in outcomes):
                        outcomes.extend(_StepOutcome(status="skipped", error="同层前序步骤失败，本步骤未执行")
                                        for _ in layer[len(outcomes):])
                        break
                if len(outcomes) != len(layer):
                    stopped = True
                    break
                if state.stopping() or any(oc.status == "cancelled" for oc in outcomes):
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
                              "result": _bounded(state.result_views.get(key, ""), _RESULT_PREVIEW_CHARS)}
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
        registry = self._background_tasks
        if registry is not None and state.registry_id:
            try:
                registry.complete(state.registry_id, success, summary, claimed=False)
            except Exception as exc:
                log(f"工作流完成通知失败: {exc}", "DEBUG", tag=_TAG)

    # ------------------------------------------------------------------
    # 单步执行（重放 / 导入 / 派发 / 门控）
    # ------------------------------------------------------------------

    async def _run_step(
            self, run_id: str, run: Dict[str, Any], step: WorkflowStepSpec,
            state: _RunState, existing: Dict[str, Dict[str, Any]],
            imports: Dict[str, Dict[str, Any]], results: Dict[str, str],
    ) -> _StepOutcome:
        try:
            outcome = await self._run_step_inner(run_id, run, step, state, existing, imports, results)
            if outcome.status == NODE_COMPLETED:
                state.result_origins[step.key] = outcome.result_origin
                state.result_views[step.key] = redact_tool_result(
                    outcome.result, sensitive_result=outcome.sensitive_result)
            return outcome
        except asyncio.CancelledError:
            if state.stopping():
                return _StepOutcome(status="cancelled", error="停止请求")
            raise  # 进程关停：上抛由执行循环留待启动收敛

    async def _run_step_inner(
            self, run_id: str, run: Dict[str, Any], step: WorkflowStepSpec,
            state: _RunState, existing: Dict[str, Dict[str, Any]],
            imports: Dict[str, Dict[str, Any]], results: Dict[str, str],
    ) -> _StepOutcome:
        context = self._step_context(step, results)
        input_desc = step_input(step, context)
        dependency_keys = set(step.depends_on)
        if step.continue_from:
            dependency_keys.add(step.continue_from)
        dependencies = {key: {"input": state.input_hashes[key], "result": content_digest(results[key]),
                              "origin": state.result_origins[key]}
                        for key in dependency_keys}
        input_hash = bind_input(step, context, dependencies)
        state.input_hashes[step.key] = input_hash
        preview = {**input_desc, "context": f"<{len(context)} 字符>"} if step.kind == "ask" else input_desc
        input_json = _bounded(sanitize_text(json.dumps(preview, ensure_ascii=False)), _INPUT_PREVIEW_CHARS)

        def _gate_passes(result_text: str) -> bool:
            """缓存结算前的门控复验：门不过的成果不可当完成（继续修复+重跑）。"""
            if step.kind != "tool" or step.gate is None:
                return True
            ok, _actual = check_gate(result_text, step.gate)
            return ok

        # 旧摘要只用于原 run 的规格核验；修订复用要求完整依赖绑定。
        prior = existing.get(step.key)
        if prior is not None:
            if prior["input_hash"] not in (input_hash, input_digest(input_desc)[:12]):
                error = (f"重放偏移：步骤 {step.key} 的 journal 记录与规格推导不一致"
                         f"（{prior['input_hash']} != {input_hash}），拒绝缓存结算")
                log(error, "ERROR", tag=_TAG)
                return _StepOutcome(status=NODE_FAILED, error=error)
            if prior["status"] == NODE_COMPLETED:
                if run.get("parent_run_id") and not str(prior["input_hash"]).startswith("v2:"):
                    return await self._refuse_receipt(run_id, step, prior, input_hash, input_json,
                                                     "旧格式修订回执缺少可核验的依赖来源；请先核验实际结果",
                                                     outcome_unknown=step.kind == "tool")
                if not prior["result_complete"]:
                    return await self._refuse_receipt(run_id, step, prior, input_hash, input_json,
                                                     "历史结果不完整或已脱敏，无法安全复用；请核验实际结果后修订运行",
                                                     outcome_unknown=step.kind == "tool")
                cached_result = str(prior.get("result_text") or "")
                if _gate_passes(cached_result):
                    results[step.key] = cached_result
                    if prior.get("delegation_id"):
                        state.delegation_ids[step.key] = str(prior["delegation_id"])
                    await self._emit_settled(run_id, step.key, prior["ordinal"],
                                             NODE_COMPLETED, cached=True)
                    return _StepOutcome(status=NODE_COMPLETED, result=cached_result, cached=True,
                                        result_origin=receipt_origin(prior))
                # 门控未过的历史轮：不当完成，换新 ordinal 进修复+重跑循环
            elif prior["status"] == NODE_FAILED:
                return _StepOutcome(status=NODE_FAILED,
                                    error=str(prior.get("error_text") or "历史失败（重放）"))
            elif self._requires_effect_verification(step, prior):
                return await self._refuse_receipt(run_id, step, prior, input_hash, input_json,
                                                 "工具已派发但结果未知，禁止自动重放；请先核验外部状态",
                                                 outcome_unknown=True)

        # 修订导入：父 run 同名步骤输入一致且门控可通过 → 成果直接入账（不重付费）
        imported = imports.get(step.key)
        source_step = state.parent_steps.get(step.key)
        if imported is not None and source_step is not None and step.kind == source_step.kind == "tool" \
                and input_digest(step_input(step, "")) == input_digest(step_input(source_step, "")) \
                and self._requires_effect_verification(step, imported):
            return await self._refuse_receipt(run_id, step, imported, input_hash, input_json,
                                             "修订源工具结果未知，禁止重复执行；请先核验外部状态",
                                             outcome_unknown=True)
        if imported is not None and imported["status"] == NODE_COMPLETED \
                and imported["input_hash"] in (input_hash, input_digest(input_desc)[:12]) \
                and not imported["result_complete"]:
            return await self._refuse_receipt(run_id, step, imported, input_hash, input_json,
                                             "修订源结果不完整或已脱敏，无法安全复用；请核验实际结果后修订运行",
                                             outcome_unknown=step.kind == "tool")
        if imported is not None and imported["status"] == NODE_COMPLETED \
                and imported["input_hash"] == input_digest(input_desc)[:12]:
            return await self._refuse_receipt(run_id, step, imported, input_hash, input_json,
                                             "旧格式修订回执缺少可核验的依赖来源，不能自动复用或重派；请先核验实际结果",
                                             outcome_unknown=step.kind == "tool")
        if imported is not None and imported["status"] == NODE_COMPLETED and imported["input_hash"] == input_hash \
                and _gate_passes(str(imported.get("result_text") or "")):
            ordinal = await self._journal.next_ordinal(run_id, step.key)
            await self._journal.admit_node(run_id, step.key, ordinal, step.kind,
                                           input_hash, input_json)
            await self._journal.settle_node(
                run_id, step.key, ordinal,
                result_text=str(imported.get("result_text") or ""),
                delegation_id=str(imported.get("delegation_id") or ""),
                usage=self._usage_of(imported),
                result_origin=receipt_origin(imported),
            )
            imported_result = str(imported.get("result_text") or "")
            results[step.key] = imported_result
            if imported.get("delegation_id"):
                state.delegation_ids[step.key] = str(imported["delegation_id"])
            await self._emit_settled(run_id, step.key, ordinal, NODE_COMPLETED, cached=True)
            return _StepOutcome(status=NODE_COMPLETED, result=imported_result, cached=True,
                                result_origin=receipt_origin(imported))

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
            if state.stopping():
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
            outcome = await self._dispatch(run, step, state, existing, context, ordinal)
            if outcome.status == "cancelled":
                # 节点行保持 running，派发事实保留，恢复时不能把取消当成回滚。
                await self._emit_settled(run_id, step.key, ordinal, "cancelled",
                                         error=_bounded(outcome.error, 400))
                return outcome
            stored_result = redact_tool_result(outcome.result, sensitive_result=outcome.sensitive_result)
            await self._journal.settle_node(
                run_id, step.key, ordinal,
                result_text=stored_result,
                error_text=_bounded(outcome.error, 4000) if outcome.error else "",
                delegation_id=outcome.delegation_id, usage=outcome.usage,
                result_complete=stored_result == outcome.result,
                outcome_unknown=outcome.outcome_unknown,
            )
            outcome.result_origin = f"{run_id}/{step.key}/{ordinal}"
            if isinstance(outcome.result, PresentedToolResult):
                outcome.result.acknowledge(stored_result)
                outcome.result = str(outcome.result)
            await self._emit_settled(run_id, step.key, ordinal, outcome.status,
                                     error=_bounded(outcome.error, 400))

            if outcome.status == NODE_COMPLETED and gate_cfg is not None:
                ok, actual = check_gate(outcome.result, gate_cfg)
                actual_view = "<敏感结果>" if outcome.sensitive_result else actual
                if ok:
                    results[step.key] = outcome.result
                    return outcome
                if attempt < max_attempts:
                    repair = await self._run_repair(run_id, step, state,
                                                    gate_cfg, actual_view, outcome.result)
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
                        f"实际 {actual_view!r}；末轮结果：{stored_result}", 2000),
                )

            if outcome.status == NODE_COMPLETED:
                results[step.key] = outcome.result
                return outcome
            if outcome.status == "cancelled":
                return outcome

            # 失败：重试预算内换 ordinal 再跑，否则本步失败
            if retries_left > 0 and outcome.retryable:
                retries_left -= 1
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                ordinal = await self._journal.next_ordinal(run_id, step.key)
                continue
            return outcome
        return _StepOutcome(status=NODE_FAILED, error="执行预算耗尽")

    def _step_context(self, step: WorkflowStepSpec, results: Dict[str, str]) -> str:
        """模型背景保持有界；完整依赖内容另行绑定，不扩张模型上下文。"""
        if step.kind != "ask":
            return ""
        return "\n\n".join(part for part in (step.context, self._upstream_context(step, results)) if part)

    def _bind_legacy_receipts(self, spec: WorkflowSpec, nodes: Dict[str, Dict[str, Any]], *, is_revision: bool) -> None:
        """从源规格与完整回执补足旧输入绑定；缺失证据不推断、不回写历史。"""
        steps = {step.key: step for step in spec.steps}
        inputs: dict[str, str] = {}
        results: dict[str, str] = {}
        for layer in topo_layers(spec):
            for key in layer:
                step, node = steps[key], nodes.get(key)
                if node is None or node["status"] != NODE_COMPLETED or not node["result_complete"]:
                    continue
                dependencies = set(step.depends_on) | ({step.continue_from} if step.continue_from else set())
                if not dependencies.issubset(inputs):
                    continue
                stored_hash = str(node["input_hash"])
                if not stored_hash.startswith("v2:"):
                    if is_revision:
                        continue  # 旧修订未绑定依赖与原始执行身份，不能从当前图反推历史验证。
                    context = self._step_context(step, results)
                    if stored_hash != input_digest(step_input(step, context))[:12]:
                        continue
                    node["input_hash"] = bind_input(step, context, {
                        dep: {"input": inputs[dep], "result": content_digest(results[dep]),
                              "origin": receipt_origin(nodes[dep])} for dep in dependencies})
                inputs[key] = str(node["input_hash"])
                results[key] = str(node.get("result_text") or "")

    @staticmethod
    def _requires_effect_verification(step: WorkflowStepSpec, node: Dict[str, Any]) -> bool:
        """未知工具效果仅在旧调用与当前工具均显式允许重放时可重试。"""
        if step.kind != "tool" or node["status"] == NODE_COMPLETED \
                or node["dispatch_state"] not in ("dispatched", "unknown"):
            return False
        from core.entity import EntityRegistry
        entity = EntityRegistry.get(step.tool)
        return not (node["replay_safe"] and entity is not None and entity.meta.get("replay_safe") is True)

    async def _refuse_receipt(self, run_id: str, step: WorkflowStepSpec, source: Dict[str, Any],
                              input_hash: str, input_json: str, error: str, *,
                              outcome_unknown: bool = False) -> _StepOutcome:
        """将无法安全恢复的证据落为失败，后续修订仍能识别未知效果。"""
        ordinal = (int(source["ordinal"]) if source["run_id"] == run_id
                   else await self._journal.next_ordinal(run_id, step.key))
        await self._journal.admit_node(run_id, step.key, ordinal, step.kind, input_hash, input_json)
        await self._journal.settle_node(
            run_id, step.key, ordinal, error_text=error, result_text=str(source.get("result_text") or ""),
            result_complete=bool(source["result_complete"]), outcome_unknown=outcome_unknown)
        await self._emit_settled(run_id, step.key, ordinal, NODE_FAILED, error=error)
        return _StepOutcome(status=NODE_FAILED, error=error, retryable=False, outcome_unknown=outcome_unknown)

    async def _run_repair(
            self, run_id: str, step: WorkflowStepSpec, state: _RunState,
            gate: GateSpec, actual: Any, last_result: str,
    ) -> _StepOutcome:
        """门控失败后的修复委托（独立 repair 节点行，成功后主步骤重跑）。"""
        goal = (gate.repair_goal or "").strip() or (
            f"工具步骤 {step.key} 的执行结果未通过门控"
            f"（{gate.field} 应为 {gate.equals!r}，实际 {actual!r}）。"
            "请分析失败原因并修复，使下次执行能通过门控。")
        context = _bounded(last_result, _REPAIR_CONTEXT_CHARS)
        key = f"{step.key}#repair"
        input_hash = input_digest({"goal": goal, "context": context, "repair_for": step.key})
        ordinal = await self._journal.next_ordinal(run_id, key)
        await self._journal.admit_node(run_id, key, ordinal, "repair", input_hash,
                                       _bounded(goal, _INPUT_PREVIEW_CHARS))
        await self._journal.append_event(run_id, "node-admitted", {
            "key": key, "ordinal": ordinal, "kind": "repair", "phase": step.phase or "",
            "input_preview": _bounded(goal, 200),
        })
        outcome = await self._dispatch_ask(goal, context, "", state, key)
        stored_result = redact_tool_result(outcome.result, sensitive_result=outcome.sensitive_result)
        await self._journal.settle_node(
            run_id, key, ordinal,
            result_text=stored_result, result_complete=stored_result == outcome.result,
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

    def _slot(self) -> asyncio.Semaphore:
        """所有步骤共享的受理容量；业务执行只在拿到槽位后创建。"""
        if self._dispatch_sem is None:
            self._dispatch_sem = asyncio.Semaphore(max(1, get_config_int("workflow_max_parallel_steps", 3)))
        return self._dispatch_sem

    async def _dispatch(
            self, run: Dict[str, Any], step: WorkflowStepSpec, state: _RunState,
            existing: Dict[str, Dict[str, Any]], context: str, ordinal: int,
    ) -> _StepOutcome:
        if step.kind == "ask":
            return await self._dispatch_ask(step.goal, context, step.agent_name,
                                            state, step.key, step.continue_from, existing,
                                            timeout=float(step.timeout or 0))
        return await self._dispatch_tool(run, step, timeout=float(step.timeout or 0),
                                         node=(state.run_id, step.key, ordinal))

    async def _dispatch_ask(
            self, goal: str, context: str, agent_name: str,
            state: _RunState, node_key: str, continue_from: str = "",
            existing: Optional[Dict[str, Dict[str, Any]]] = None,
            timeout: float = 0.0,
    ) -> _StepOutcome:
        manager = self._delegation_manager
        if manager is None:
            return _StepOutcome(status=NODE_FAILED, error="委托管理器未初始化")
        registry = self._background_tasks
        delegation_id = ""
        if registry is not None:
            delegation_id = registry.register(
                state.scope, "delegation", f"[{state.name}] {goal}"[:80])

        base_messages = self._continuation_messages(state, existing or {}, continue_from, goal, context)
        result: Optional[Any] = None
        try:
            async with asyncio.timeout(timeout if timeout > 0 else None):
                async with self._slot():
                    if delegation_id:
                        state.delegation_ids[node_key] = delegation_id
                    result = await manager.delegate(
                        goal, context, agent_name=agent_name, scope_hint=state.scope,
                        delegation_id=delegation_id, base_messages=base_messages,
                    )
        except TimeoutError:
            return _StepOutcome(status=NODE_FAILED,
                                error=f"步骤超时（{timeout:g}s），操作结果未确认，请先核验: {goal[:120]}",
                                delegation_id=delegation_id, retryable=False)
        finally:
            if delegation_id and registry is not None:
                preview = ""
                if result is not None:
                    preview = ((result.output if result.success else result.error) or "")[:1500]
                try:
                    registry.complete(delegation_id, bool(result and result.completion_ready),
                                      preview, claimed=True)
                except Exception:
                    pass  # 注册表收尾失败不影响步骤结局

        if result.cancelled:
            return _StepOutcome(status="cancelled", error=result.error or "已取消",
                                delegation_id=delegation_id)
        if not result.success:
            return _StepOutcome(status=NODE_FAILED, error=result.error or "子代理执行失败",
                                delegation_id=delegation_id,
                                retryable=result.completed_reason == "completed")
        state.delegation_ids[node_key] = delegation_id
        usage = dict(getattr(result, "usage", {}) or {})
        if not result.completion_ready:
            detail = result.schema_error or result.completed_reason
            return _StepOutcome(
                status=NODE_FAILED, result=result.output, delegation_id=delegation_id,
                usage=usage or None, retryable=False,
                error=f"子代理尚未满足完成契约（{detail}）；已保留进展，"
                      "请先核验并续跑补交，未自动重试以避免重复执行",
            )
        return _StepOutcome(status=NODE_COMPLETED, result=result.output,
                            delegation_id=delegation_id, usage=usage or None)

    def _continuation_messages(
            self, state: _RunState, existing: Dict[str, Dict[str, Any]],
            continue_from: str, goal: str, context: str,
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
                       f"请在既有进展基础上继续完成：\n{goal}"
                       + (f"\n[当前步骤上下文]\n{context}" if context else ""),
            "_source": {"origin": "steer"},
        })
        return messages

    async def _dispatch_tool(
            self, run: Dict[str, Any], step: WorkflowStepSpec, timeout: float = 0.0,
            *, node: tuple[str, str, int] | None = None,
    ) -> _StepOutcome:
        started = False
        sensitive_result = False
        try:
            async with asyncio.timeout(timeout if timeout > 0 else None):
                async with self._slot():
                    decision = await self._request_approval(step, run)
                    if not decision.allowed:
                        return _StepOutcome(status=NODE_FAILED, error=f"权限检查拒绝: {decision.reason}", retryable=False)
                    from core.entity import EntityRegistry
                    arguments = json.dumps(step.args, ensure_ascii=False)
                    entity = EntityRegistry.get(step.tool)
                    sensitive_result = bool(entity and entity.meta.get("sensitive_result") is True)
                    if node is not None:
                        await self._journal.mark_dispatched(
                            *node, replay_safe=bool(entity and entity.meta.get("replay_safe") is True))
                    started = True
                    result_str = await EntityRegistry.execute_tool(step.tool, arguments)
        except TimeoutError:
            detail = "操作结果未确认，请先核验" if started else "排队或权限检查未完成，工具尚未执行"
            return _StepOutcome(status=NODE_FAILED, error=f"步骤超时（{timeout:g}s），{detail}: {step.tool}",
                                retryable=not started, outcome_unknown=started)
        parsed = parse_tool_result_json(result_str)
        unknown = isinstance(parsed, dict) and parsed.get("outcome") == "unknown"
        error = extract_error_text(parsed) or ("工具结果未确认" if unknown else "")
        if error:
            retryable = isinstance(parsed, dict) and parsed.get("retryable") is not False \
                and parsed.get("outcome") != "unknown"
            return _StepOutcome(status=NODE_FAILED, error=error[:4000], result=result_str, retryable=retryable,
                                outcome_unknown=unknown,
                                sensitive_result=sensitive_result)
        if decision.notice:
            source_result = result_str
            payload = parsed if isinstance(parsed, dict) else {"result": result_str}
            payload["permission_notice"] = decision.notice
            result_str = json.dumps(payload, ensure_ascii=False)
            if isinstance(source_result, PresentedToolResult):
                def acknowledge(visible: str) -> None:
                    delivered = parse_tool_result_json(visible)
                    text = delivered.get("result", "") if isinstance(delivered, dict) else ""
                    source_result.acknowledge(visible if isinstance(parsed, dict) else text if isinstance(text, str) else "")
                result_str = PresentedToolResult(result_str, acknowledge)
        return _StepOutcome(status=NODE_COMPLETED, result=result_str, sensitive_result=sensitive_result)

    async def _request_approval(self, step: WorkflowStepSpec, run: Dict[str, Any]) -> ApprovalResult:
        """工具步骤使用与对话相同的规则和 AI 评审。"""
        from agent.approval import get_approval_gate
        return await get_approval_gate().check(
            tool_name=step.tool, tool_args=dict(step.args),
            reason=f"工作流[{run['name']}] 步骤 {step.key}", scope=str(run.get("scope") or ""),
        )

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

    def _upstream_context(self, step: WorkflowStepSpec, results: Dict[str, str]) -> str:
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

    async def save_definition(self, spec_data: Any, workflow_id: str = "") -> Dict[str, Any]:
        """创建或替换可复用定义；编辑不影响已启动运行的规格快照。"""
        spec = parse_spec(spec_data)
        create = not workflow_id
        workflow_id = workflow_id or uuid.uuid4().hex[:12]
        async with self._lifecycle_lock:
            await self._journal.save_definition(workflow_id, spec.name, canonical_spec(spec), create=create)
            return await self._journal.get_definition(workflow_id)

    async def list_definitions(self, limit: int = 30, offset: int = 0) -> List[Dict[str, Any]]:
        return await self._journal.list_definitions(max(1, min(limit, 100)), max(0, offset))

    async def definition_detail(self, workflow_id: str) -> Dict[str, Any]:
        return await self._journal.get_definition(workflow_id)

    async def delete_definition(self, workflow_id: str) -> Dict[str, Any]:
        """仅删除可复用定义，保留所有运行及其规格快照。"""
        async with self._lifecycle_lock:
            await self._journal.delete_definition(workflow_id)
        return {"ok": True, "workflow_id": workflow_id}

    async def delete_run(self, run_id: str) -> Dict[str, Any]:
        """与启动/续跑互斥，等待执行任务完全收敛后才允许删除。"""
        async with self._lifecycle_lock:
            if run_id in self._runs:
                raise ValueError("工作流仍在执行或结算中，请停止并等待结算后再删除")
            await self._journal.delete_run(run_id)
        return {"ok": True, "run_id": run_id}

    async def cleanup_runs(self, days: int = 30, *, include_stopped: bool = False) -> Dict[str, Any]:
        """按保留天数清理运行历史，始终保护在飞运行及恢复所需的源记录。"""
        async with self._lifecycle_lock:
            count = await self._journal.cleanup_runs(
                days, include_stopped=include_stopped, protected_ids=self.running_ids())
        return {"ok": True, "deleted": count}

    async def list_runs(self, limit: int = 30, offset: int = 0) -> List[Dict[str, Any]]:
        runs = await self._journal.list_runs(max(1, min(limit, 100)), max(0, offset))
        for item in runs:
            # journal 行键为 id；对外契约统一 run_id（与 _run_summary/start/resume 一致）
            item["run_id"] = item.pop("id")
            item["running"] = item["run_id"] in self._runs
        return runs

    async def run_detail(self, run_id: str) -> Dict[str, Any]:
        run = await self._journal.get_run(run_id)
        if run is None:
            raise ValueError(f"工作流不存在: {run_id}")
        nodes = await self._journal.nodes_of_run(run_id)
        events = await self._journal.list_events(run_id, limit=400, latest=True)
        return {
            "run": {**self._run_summary(run), "running": run_id in self._runs},
            "spec": json.loads(run["spec_json"]),
            "nodes": [
                {
                    "key": n["step_key"], "ordinal": n["ordinal"], "kind": n["kind"],
                    "status": n["status"],
                    "result": _bounded(str(n.get("result_text") or ""), _NODE_RESULT_PREVIEW_CHARS),
                    "error": str(n.get("error_text") or ""),
                    "usage": self._usage_of(n),
                    "delegation_id": n.get("delegation_id") or "",
                    "created_at": n["created_at"], "updated_at": n["updated_at"],
                }
                for n in nodes
            ],
            "events": events,
            "events_truncated": bool(events and events[0]["sequence"] > 1),
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
            "description": "工作流步骤并发上限（工具、ask 委托与修复共用；非并发安全工具在同层串行）",
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
