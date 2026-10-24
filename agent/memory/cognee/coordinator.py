"""SQLite 权威存储到 Cognee 的持久化投影协调器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Optional

from core.activity import activity_owner, activity_scope, activity_step
from core.log import log

from ..memory_store import MemoryStore
from ..projection import projection_dataset
from ..store.projection_state import ProjectionState
from .client import CogneeClient
from .config import CogneeConfig
from .errors import error_text as _error_text
from .rebuild import CogneeRebuild
from .retirement import DatasetRetirement
from .storage import cognee_storage_stats
from .types import CogneeMaintenanceStatus, CogneeSyncStatus
from .write_breaker import WriteBreaker
from .writer import ProjectionWriter


class CogneeCoordinator:
    """消费 MemoryStore outbox，并维护 Cognee 数据映射。"""

    def __init__(
        self,
        store: MemoryStore,
        client: CogneeClient,
        config: CogneeConfig,
    ) -> None:
        self.store = store
        self.client = client
        self.config = config.normalized()
        self.rebuild = CogneeRebuild(store, client)
        self._task: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._closing = False
        self._stopping = False
        self._projection_lock = asyncio.Lock()
        self._configuration_task: asyncio.Task[None] | None = None
        self._pending_config: CogneeConfig | None = None
        self._configuration_error = ""
        self._last_error = ""
        self._last_improve_ns: dict[str, int] = {}
        self._maintenance_task: asyncio.Task[dict[str, Any]] | None = None
        self._maintenance_status: CogneeMaintenanceStatus | None = None
        self._last_compact_ns: int | None = None
        self._last_cleanup_ns: int | None = None
        self._maintenance_cost_ns = 0
        # 空闲窗口作业（run_in_idle_window 提交，worker 在队列排空后执行）
        self._idle_job: Optional[Any] = None
        self._idle_done = asyncio.Event()
        self._idle_started = False
        # 写盘熔断（暂停投影认领与自动压缩，冷却到期自动恢复）
        self._breaker = self._build_breaker()
        self._pause_until_ns = 0
        self._pause_reason = ""
        self.last_compact_at = 0.0
        self.last_compact_summary = ""
        self.writer = ProjectionWriter(store, client, self.config,
                                       observe_write=lambda: self._breaker.observe(), after_write=self._maybe_improve)
        self.retirement = DatasetRetirement(store, client, self._projection_lock, self._retirement_state,
                                           self.rebuild.verify_backend)

    def _retirement_state(self) -> ProjectionState:
        self._require_open()
        state = self.rebuild.state
        if self.config.sync_enabled or self.is_running or self.configuration_pending:
            raise ValueError("请先暂停同步并等待当前批次结束，再清理旧轮次")
        if not state or state.rebuild_requested:
            raise ValueError("构建请求尚未稳定，不能清理旧轮次")
        if state.phase != "ready" and not (
            state.phase in {"building", "failed"} and state.memory_scanned
            and state.graph_scanned and state.backup_path
        ):
            raise ValueError("请先完成原生内容扫描与备份，再清理旧轮次")
        return state

    @property
    def is_running(self) -> bool:
        """同步 worker 是否在运行。"""
        return self._task is not None and not self._task.done()

    def _build_breaker(self) -> WriteBreaker:
        return WriteBreaker(
            self.config.write_breaker_threshold_mb,
            self.config.write_breaker_window_seconds,
            sampler=self.client.sample_write_bytes,
        )

    async def start(self) -> None:
        self.client.projection_ready = False
        self.store.set_cognee_projection_enabled(
            self.config.enabled,
        )
        if not self.config.enabled:
            return
        await self.rebuild.initialize(self.config)
        availability = await self.client.initialize()
        if availability.ready:
            await self.client.available_datasets([])
        if not availability.ready and not availability.configuration_required:
            self._last_error = availability.reason
        # 启动即预热存储统计（大库遍历数十秒，后台线程执行不阻塞启动），
        # 保证用户打开数据管理/记忆页时快照已就绪
        cognee_storage_stats.schedule_refresh(self.config.absolute_data_root)
        if self.config.sync_enabled and self._task is None:
            self._task = asyncio.create_task(
                self._worker(),
                name="memory.cognee.sync",
            )

    def _stale_after_seconds(self) -> float:
        """卡死判定阈值（processing 超过该时长视为被中断）。

        必须与批次最坏耗时联动：单批次可能顺序执行 add + cognify + improve
        三次流水线，阈值过低会把仍在正常处理的长批次误判为卡死并重复认领。
        """
        from core.config import get_config_float
        configured = get_config_float("cognee_sync_stale_seconds", 900.0)
        batch_worst_case = 3 * self.config.pipeline_timeout_seconds + 600.0
        return max(configured, batch_worst_case)

    def quiesce(self) -> None:
        """立即停止批次和新作业；未提交 outbox 保留到下次启动重试。"""
        if self._stopping:
            return
        self._stopping = True
        self._closing = True
        self._wake.set()
        self._pending_config = None
        self.client.quiesce()
        for task in (self._task, self._configuration_task, self._maintenance_task):
            if task and not task.done():
                task.cancel()

    def _require_open(self) -> None:
        if self._stopping:
            raise RuntimeError("Cognee 正在安全关停，不再接纳同步或维护任务")

    async def close(self) -> None:
        self.quiesce()
        await self.retirement.close()
        if self._configuration_task:
            await asyncio.gather(self._configuration_task, return_exceptions=True)
            self._configuration_task = None
        self._pending_config = None
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._maintenance_task:
            await asyncio.gather(self._maintenance_task, return_exceptions=True)
            self._maintenance_task = None
        self._maintenance_status = None
        # 唤醒可能仍在等待空闲窗口作业的调用方，避免关停悬挂
        if self._idle_job is not None:
            if asyncio.iscoroutine(self._idle_job):
                self._idle_job.close()
            self._idle_job = asyncio.CancelledError("cognee worker 已关闭")
            self._idle_done.set()
        async with self._projection_lock:
            log("Cognee 上层任务已停止，正在回收独立索引工作进程", tag="关停")
            await self.client.close()
            log("Cognee 数据库已安全关闭；未提交批次将在下次启动重试", tag="关停")

    async def reconfigure(self, config: CogneeConfig) -> None:
        """接纳已保存配置，在当前投影批次结束后应用最后一次修改。"""
        self._require_open()
        self.rebuild.validate_config(config)
        self._pending_config = config.normalized()
        self._configuration_error = ""
        if self._configuration_task is None or self._configuration_task.done():
            self._configuration_task = asyncio.create_task(
                self._drain_configuration(), name="memory.cognee.configure",
            )

    @property
    def configuration_pending(self) -> bool:
        """已保存配置是否还在等待安全切换或初始化。"""
        return self._configuration_task is not None and not self._configuration_task.done()

    async def _drain_configuration(self) -> None:
        while self._pending_config is not None:
            async with self._projection_lock:
                config = self._pending_config
                self._pending_config = None
                self._configuration_error = ""
                try:
                    await self._apply_configuration(config)
                except Exception as exc:
                    self._configuration_error = _error_text(exc)
                if self._configuration_error:
                    log(f"Cognee 配置已保存但应用失败: {self._configuration_error}", "WARNING", tag="思维")

    async def _apply_configuration(self, config: CogneeConfig) -> None:
        self.config = config.normalized()
        self.writer.config = self.config
        self.store.set_cognee_projection_enabled(
            self.config.enabled,
        )
        self.client.reconfigure(self.config)
        if self.config.enabled:
            await self.rebuild.initialize(self.config)
        self._breaker = self._build_breaker()
        self._pause_until_ns = 0
        self._pause_reason = ""

        should_run = self.config.enabled and self.config.sync_enabled
        if should_run and (self._task is None or self._task.done()):
            self._closing = False
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._worker(), name="memory.cognee.sync")
        elif not should_run and self._task and not self._task.done():
            self._closing = True
            self._wake.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # 取消属正常关闭流程（正常控制流，非异常）
            self._task = None

        # 立即按新配置做一次初始化，让状态接口即时反馈解析结果
        if self.config.enabled:
            availability = await self.client.initialize()
            if availability.ready:
                await self.client.list_datasets()
            self._configuration_error = "" if availability.ready else availability.reason
            self._last_error = (
                "" if availability.ready or availability.configuration_required else availability.reason
            )
        else:
            self._last_error = ""

    def wake(self) -> None:
        self._wake.set()

    async def status(self) -> CogneeSyncStatus:
        active = await self.writer.progress.snapshot(self.client.pipeline_progress)
        counts = await self.store.get_cognee_sync_status()
        activity = await self.store.get_cognee_sync_activity()
        paused = self._pause_until_ns > time.monotonic_ns()
        paused_until = 0.0
        if paused:
            paused_until = time.time() + (self._pause_until_ns - time.monotonic_ns()) / 1e9
        return CogneeSyncStatus(
            enabled=self.config.enabled and self.config.sync_enabled,
            running=bool(self._task and not self._task.done()),
            pending=counts["pending"],
            failed=counts["failed"],
            synced=counts["synced"],
            processing=activity["processing"],
            retrying=activity["retrying"],
            next_retry_at=activity["next_retry_at"],
            active=active,
            publication=self.client.publication_status,
            pause_requested=bool(self._pending_config and not (
                self._pending_config.enabled and self._pending_config.sync_enabled)),
            configuration_pending=self.configuration_pending,
            configuration_error=self._configuration_error,
            last_error=activity["last_error"] or self._last_error,
            last_compact_at=self.last_compact_at,
            last_compact_summary=self.last_compact_summary,
            maintenance=self._maintenance_status,
            paused=paused,
            paused_until=paused_until,
            pause_reason=self._pause_reason if paused else "",
        )

    async def request_compact(self, *, prune_only: bool = False) -> dict[str, Any]:
        """登记单次后台维护；立即响应，在批次边界互斥执行，重复请求不累积。"""
        self._require_open()
        if not self.config.enabled:
            return {"ok": False, "error": "cognee 未启用"}
        if not self._projection_allowed():
            return {"ok": False, "error": self._pause_reason}
        mode = "cleanup" if prune_only else "compact"
        if self._maintenance_status and self._maintenance_status.mode != mode:
            return {"ok": False, "error": "已有其他索引维护任务，请等待完成后再操作"}
        self._start_maintenance(prune_only=prune_only)
        return {"ok": True, "scheduled": True}

    def _start_maintenance(self, *, prune_only: bool) -> asyncio.Task[dict[str, Any]]:
        """自动与手动维护共用单槽任务，由协调器生命周期回收。"""
        if self._maintenance_task and not self._maintenance_task.done():
            return self._maintenance_task
        self._maintenance_status = CogneeMaintenanceStatus(mode="cleanup" if prune_only else "compact")

        async def run() -> dict[str, Any]:
            try:
                result = await self._run_compact(prune_only=prune_only)
                if "error" in result:
                    self.last_compact_at = time.time()
                    self.last_compact_summary = f"失败: {result['error']}"
                return result
            except Exception as exc:
                self.last_compact_at = time.time()
                self.last_compact_summary = f"失败: {_error_text(exc)}"
                log(f"Cognee 后台维护失败: {self.last_compact_summary}", "WARNING", tag="记忆")
                return {"error": self.last_compact_summary}

        def finished(task: asyncio.Task[dict[str, Any]]) -> None:
            # 创建后尚未运行即被取消时，协程内部 finally 不会执行。
            if self._maintenance_task is task:
                self._maintenance_status = None

        self._maintenance_task = asyncio.create_task(run(), name="memory.cognee.maintenance")
        self._maintenance_task.add_done_callback(finished)
        return self._maintenance_task

    async def run_in_idle_window(self, job: Any, *, check_cancel: Callable[[], None] | None = None) -> Any:
        """在队列排空后的空闲窗口执行 job（协程，与写入互斥）。

        供存储卷备份/迁移等需要 cognee 数据树静默期的操作使用：
        worker 存活时提交作业并等待其在空闲窗口执行完毕（作业与批次
        串行，投影锁同时隔离手动维护）；worker 未运行时持锁执行。
        """
        if self._stopping:
            if asyncio.iscoroutine(job):
                job.close()
            self._require_open()
        if not (self._task and not self._task.done()):
            try:
                async with self._projection_lock:
                    self._require_open()
                    async with self.client.idle_snapshot():
                        return await job
            finally:
                if asyncio.iscoroutine(job):
                    job.close()
        if self._idle_job is not None:
            if asyncio.iscoroutine(job):
                job.close()
            raise RuntimeError("已有空闲窗口作业排队中")
        self._idle_done.clear()
        self._idle_started = False
        self._idle_job = job
        self.wake()
        while not self._idle_done.is_set():
            if check_cancel is not None and not self._idle_started:
                try:
                    check_cancel()
                except Exception:
                    self._idle_job = None
                    if asyncio.iscoroutine(job):
                        job.close()
                    raise
            try:
                await asyncio.wait_for(self._idle_done.wait(), timeout=0.2)
            except TimeoutError:
                continue
        result = self._idle_job
        self._idle_job = None
        if isinstance(result, BaseException):
            raise result
        return result

    async def _run_idle_job(self) -> None:
        """worker 侧：执行排队的空闲作业并唤醒等待方（异常原样回传）。"""
        job = self._idle_job
        if job is None:
            return
        self._idle_started = True
        try:
            async with self._projection_lock:
                async with self.client.idle_snapshot():
                    self._idle_job = await job
        except BaseException as exc:
            self._idle_job = exc
        finally:
            self._idle_done.set()

    async def retry_failed(self) -> int:
        self._require_open()
        self.client.reset_fault()
        count = await self.store.retry_failed_cognee_sync()
        self.wake()
        return count

    async def repair_coverage(self, namespace: str) -> dict[str, Any]:
        """空闲时审计并原子补队；同步暂停时只入队，继续同步后消费。"""
        self._require_open()
        if self._projection_lock.locked() or self.retirement.status.running or self._pending_config:
            raise ValueError("索引正在同步、维护或应用配置，请等待完成后重试")
        async with self._projection_lock:
            self._require_open()
            count = await self.rebuild.repair_coverage(self.config, namespace)
        self.wake()
        return {"queued": count, "namespace": namespace, "paused": not self.config.sync_enabled}

    async def request_rebuild(self, *, restart: bool = False) -> dict[str, Any]:
        """持久化构建请求；不等待耗时的在途批次，完成状态由验收产生。"""
        self._require_open()
        if self.retirement.status.running:
            raise ValueError("旧数据清理正在执行，请完成后再请求构建")
        self.client.reset_fault()
        state = await self.rebuild.schedule(self.config, restart=restart)
        self.wake()
        return {"ok": True, "scheduled": True, "rebuild": state.model_dump()}

    async def backfill(self, *, limit: int = 0, dry_run: bool = True) -> dict[str, int | bool]:
        self._require_open()
        if dry_run:
            counts = await self.store.cognee_inventory_counts()
            total = counts["memories"] + counts["graph_nodes"]
            return {"dry_run": True, **counts, "eligible": min(total, limit) if limit > 0 else total}
        count = await self.store.enqueue_cognee_backfill(limit=limit)
        self.wake()
        return {"dry_run": False, "queued": count}

    async def improve(self, dataset_name: str, *, review_conflicts: bool = False,
                      build_global_context_index: bool = True) -> Any:
        """在投影与维护互斥窗口中增强图谱，遵循写盘熔断。"""
        async with self._projection_lock:
            self._require_open()
            namespace = self.client.projection_namespace or self.config.dataset_prefix
            if not self.config.enabled or not self.client.projection_ready or dataset_name not in {
                projection_dataset(namespace), projection_dataset(namespace, graph=True),
            }:
                raise ValueError("只能增强已验收的当前代际，请先检查 Cognee 构建状态")
            if not self._projection_allowed():
                raise RuntimeError(self._pause_reason)
            return await self._enhance(dataset_name, review_conflicts=review_conflicts,
                                       build_global_context_index=build_global_context_index)

    async def _build_global_context(self, dataset_name: str) -> Any:
        return await self._enhance(dataset_name, build_global_context_index=True)

    async def _enhance(self, dataset_name: str, *, build_global_context_index: bool = False,
                       review_conflicts: bool = False) -> Any:
        """只增强显式选择的阶段；冲突审查保留原始证据，不自动裁决权威内容。"""
        result = await self.client.improve(dataset=dataset_name,
            build_global_context_index=build_global_context_index, review_conflicts=review_conflicts)
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        stages = {s.get("stage"): s for s in value.get("stages", [])} if isinstance(value, dict) else {}
        requested = [name for name, enabled in (("global_context_index", build_global_context_index),
                                                ("review_conflicts", review_conflicts)) if enabled]
        for name in requested:
            if stages.get(name, {}).get("status") not in {"completed", "already_completed"}:
                raise RuntimeError(f"Cognee 增强阶段未完成 ({name}): {stages.get(name, value)}")
        return value

    def _projection_allowed(self) -> bool:
        """写盘熔断闸门：暂停期间拒绝批次认领与自动压缩。

        采样点覆盖 worker 轮次与管线边界（add/cognify 前后），长批次内
        的短时风暴按速率口径无需等满窗口即可判定；冷却到期重评，仍超
        限则续停。psutil 不可用时熔断恒放行（fail-open）。
        """
        self._breaker.observe()
        if not self.config.write_breaker_enabled:
            return True
        now_ns = time.monotonic_ns()
        if now_ns < self._pause_until_ns:
            return False
        if self._pause_until_ns:
            self._pause_until_ns = 0
            self._pause_reason = ""
        tripped, rate_mb_s = self._breaker.over_threshold()
        if tripped:
            cooldown_ns = int(self.config.write_breaker_cooldown_seconds * 1e9)
            self._pause_until_ns = now_ns + cooldown_ns
            self._pause_reason = (
                f"写盘速率 {rate_mb_s:.1f}MB/s 超阈值 "
                f"{self.config.write_breaker_threshold_mb:.0f}MB/"
                f"{self.config.write_breaker_window_seconds:.0f}s，"
                f"冷却 {self.config.write_breaker_cooldown_seconds / 60:.0f} 分钟"
            )
            log(
                f"Cognee 写盘熔断触发，投影暂停: {self._pause_reason}",
                "WARNING",
                tag="思维",
            )
        return not tripped

    async def _worker(self) -> None:
        recovering = True
        while not self._closing:
            try:
                # 宿主保证单消费者：新 worker 无在途批次，可立即续跑中断任务。
                await self.store.requeue_stale_cognee_sync(0 if recovering else self._stale_after_seconds())
                recovering = False
                # 模型待配置时保留 pending，不消耗条目的重试次数；配置齐后自动恢复。
                availability = await self.client.initialize()
                consumed = False
                if availability.ready:
                    async with self._projection_lock:
                        if self._stopping:
                            break
                        if self.rebuild.state and self.rebuild.state.rebuild_requested:
                            await self.rebuild.request(self.config)
                        await self.rebuild.advance()
                        if self.rebuild.can_consume and self._projection_allowed():
                            batch = await self.store.claim_cognee_sync_batch(self.config.sync_batch_size)
                            if batch:
                                await self._process_entries(batch)
                                consumed = True
                    if self.rebuild.can_consume and not self._stopping:
                        await self._maybe_compact(busy=consumed)
                    if consumed:
                        continue
                # 空闲作业是用户显式请求（备份/迁移），不受熔断阻断
                await self._run_idle_job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = _error_text(exc)
                log(f"Cognee 同步循环异常: {self._last_error}", "WARNING", tag="思维")

            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.config.sync_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass  # 超时属正常等待结束（正常控制流，非异常）

    async def _maybe_compact(self, *, busy: bool = False) -> None:
        """繁忙时只清理版本；自动维护冷却至少为上次总耗时的九倍，避免抢占同步。"""
        if not self.config.enabled or not self.config.compact_enabled or self._maintenance_status is not None:
            return
        now_ns = time.monotonic_ns()
        interval_ns = int(self.config.compact_interval_seconds * 1e9)
        due = self._last_compact_ns is None or now_ns - self._last_compact_ns >= interval_ns
        cleanup_interval = max(int(self.config.compact_cleanup_interval_seconds * 1e9),
                               self._maintenance_cost_ns * 9)
        cleanup_due = self._last_cleanup_ns is None or now_ns - self._last_cleanup_ns >= cleanup_interval
        full = due and not busy
        if not (full or cleanup_due):
            return
        await self._start_maintenance(prune_only=not full)

    async def _run_compact(self, *, prune_only: bool = False) -> dict[str, Any]:
        with activity_owner(kind="maintenance", label="Cognee", owner_id="cognee"):
            async with activity_scope(uuid.uuid4().hex[:8], "") as activity, activity_step("cognee_compact") as step:
                async with self._projection_lock:
                    self._require_open()
                    if not self.config.enabled:
                        raise RuntimeError("Cognee 已停用，取消尚未执行的维护")
                    if self._maintenance_status:
                        self._maintenance_status.phase = "running"
                    if not self._projection_allowed():
                        step.status, step.detail = "error", self._pause_reason
                        activity.status, activity.error = "failed", self._pause_reason
                        return {"error": self._pause_reason}
                    result = await self._compact_storage(prune_only=prune_only)
                    step.detail = self.last_compact_summary
                    if "error" in result:
                        step.status = "error"
                        activity.status, activity.error = "failed", str(result["error"])
                    return result

    async def _compact_storage(self, *, prune_only: bool = False) -> dict[str, Any]:
        started = self._last_cleanup_ns = time.monotonic_ns()
        if not prune_only:
            self._last_compact_ns = self._last_cleanup_ns
        log(
            "Cognee LanceDB 维护开始（仅清理历史版本）" if prune_only
            else "Cognee LanceDB 压缩开始（有界合并碎片并回收历史版本）",
            tag="思维",
        )
        try:
            result = await self.client.compact_vector_storage(
                self.config.compact_retention_days, prune_only=prune_only,
            )
        except Exception as exc:
            self.last_compact_summary = f"失败: {_error_text(exc)}"
            log(
                f"Cognee LanceDB 压缩失败（下个周期重试）: {self.last_compact_summary}",
                "WARNING",
                tag="思维",
            )
            return {"error": self.last_compact_summary}
        finally:
            # 从完成时开始冷却，长维护不会结束后立即又被判定到期。
            self._last_cleanup_ns = time.monotonic_ns()
            self._maintenance_cost_ns = self._last_cleanup_ns - started
            if not prune_only:
                self._last_compact_ns = self._last_cleanup_ns
        cognee_storage_stats.schedule_refresh(self.config.absolute_data_root)
        self.last_compact_at = time.time()
        self._breaker.observe()
        reclaimed_mb = result["bytes_reclaimed"] / 1024 / 1024
        self.last_compact_summary = (
            f"表 {result['tables']}，回收 {reclaimed_mb:.1f}MB"
            + ("，已达单轮预算，余量留待下次维护" if result.get("budget_exhausted") else "")
            + (f"，{len(result['errors'])} 个错误" if result["errors"] else "")
        )
        log(
            f"Cognee LanceDB 维护完成: 库 {result['databases']} 表 {result['tables']}，"
            f"清理旧文件 {reclaimed_mb:.1f}MB，总耗时 {self._maintenance_cost_ns / 1e9:.1f}s"
            + (f"，错误: {result['errors']}" if result["errors"] else ""),
            tag="思维",
        )
        return result

    async def _process_batch(self, batch: list[dict[str, Any]]) -> None:
        async with self._projection_lock:
            await self._process_entries(batch)

    async def _process_entries(self, batch: list[dict[str, Any]]) -> None:
        await self.writer.process(batch)
        self._last_error = self.writer.last_error

    async def _maybe_improve(self, dataset_name: str) -> None:
        """按数据集防抖运行图谱增强；增强失败不影响已完成的投影。

        全局上下文摘要有额外模型成本，且 CHUNKS 类召回不依赖它，
        按 improve_interval_seconds 限频（<=0 时同步路径不运行）。
        """
        interval = self.config.improve_interval_seconds
        if interval <= 0:
            return
        now_ns = time.time_ns()
        last_ns = self._last_improve_ns.get(dataset_name, 0)
        if now_ns - last_ns < int(interval * 1e9):
            return
        self._last_improve_ns[dataset_name] = now_ns
        try:
            await self._build_global_context(dataset_name)
        except Exception as exc:
            log(
                f"Cognee 数据集 {dataset_name} 图谱增强失败（不影响投影）: {_error_text(exc)}",
                "WARNING",
                tag="思维",
            )
