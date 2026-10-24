"""宿主 Cognee 门面：进程隔离、索引提交与失败恢复。

模型看到什么：沿用既有记忆工具与召回结果，维护窗口退回原生检索。
token 影响：提交与恢复不调用模型；未提交批次可能重新抽取。
缓存影响：不改变提示词及稳定前缀。
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

from filelock import FileLock
from pydantic import TypeAdapter

from core.log import log

from ..projection import projection_dataset
from .checkpoints import IndexCheckpoints
from .config import CogneeConfig
from .llm_bridge import CogneeConfigError, summarize_resolved
from .manifest import IndexManifest, MutationPlan
from .model_config import resolve_models, validate_models
from .process import CogneeProcess, WorkerCrashed
from .records import DatasetCatalog, DatasetRecord, DocumentRecord, IngestionResult, document_processed
from .types import CogneeAvailability, CogneeIndexBusy, CogneePipelineProgress, CogneeRecallItem
from .wire import InputDocument, make_document

T = TypeVar("T")
_DATASETS = TypeAdapter(list[DatasetRecord])
_DOCUMENTS = TypeAdapter(list[DocumentRecord])
_INGESTION = TypeAdapter(IngestionResult)


async def _disk_job(job: Callable[[], T]) -> T:
    """取消不释放文件操作互斥；等待复制线程退出，避免恢复与复制互相覆盖。"""
    task = asyncio.create_task(asyncio.to_thread(job))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class CogneeClient:
    """宿主只拥有配置、RPC 与发布事务；SDK 实现不进入继承或调用面。"""

    def __init__(self, config: CogneeConfig, *,
                 source_mappings: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None) -> None:
        self.config = config.normalized()
        self.projection_ready = True
        self.projection_namespace = ""
        self._source_mappings = source_mappings
        self._closing = False
        self._configured = False
        self._configuration_required = False
        self._import_error = ""
        self._initialize_lock = asyncio.Lock()
        self._datasets_lock = asyncio.Lock()
        self._dataset_names: set[str] = set()
        self._datasets_until = 0.0
        self._catalog: list[DatasetRecord] = []
        self._catalog_at = 0.0
        self._summary_datasets: set[str] = set()
        self._compact_rotation = 0
        self._chat_payload: dict[str, Any] = {}
        self._embedding_payload: dict[str, Any] = {}
        self._resolved_chat: dict[str, Any] = {}
        self._resolved_embedding: dict[str, Any] = {}
        self._operation_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._transaction: ContextVar[asyncio.Task[Any] | None] = ContextVar("cognee_index_transaction", default=None)
        self._process: CogneeProcess | None = None
        self._checkpoints: IndexCheckpoints | None = None
        self._owner: FileLock | None = None
        self._fault = ""
        self._phase = "idle"
        self._revision = 0
        self._worker_revision = -1
        self._plan: MutationPlan | None = None
        self._transaction_writes = False
        self._finished_writes = 0
        self._needs_recovery = False
        self._reads: set[asyncio.Task[Any]] = set()

    def sample_write_bytes(self) -> int:
        import psutil

        active = self._process.sample_writes() if self._process else 0
        return int(psutil.Process().io_counters().write_bytes) + self._finished_writes + active

    def reconfigure(self, config: CogneeConfig) -> None:
        if self.config.absolute_data_root != config.absolute_data_root:
            self._catalog, self._catalog_at = [], 0.0
        self.config = config.normalized()
        self._configured = False
        self._import_error = ""
        self._configuration_required = False
        self._resolved_chat, self._resolved_embedding = {}, {}
        self._datasets_until = 0
        self._summary_datasets.clear()
        self._revision += 1
        self.reset_fault()

    @property
    def publication_status(self) -> dict[str, Any]:
        state = self._checkpoints.state if self._checkpoints else {}
        return {"phase": self._phase, "error": self._fault,
                "commits": max(0, state.get("commits", 0) - 1),
                "cloned_bytes": state.get("cloned_bytes", 0), "copied_bytes": state.get("copied_bytes", 0)}

    def reset_fault(self) -> None:
        """显式重试或新建代际才解开原生崩溃熔断，不自动反复打开坏库。"""
        self._fault = ""

    async def _configure(self) -> None:
        chat, embedding = resolve_models(self.config)
        if self._chat_payload != chat or self._embedding_payload != embedding:
            self._revision += 1
        self._chat_payload, self._embedding_payload = chat, embedding
        self._resolved_chat = summarize_resolved(chat, kind="chat")
        self._resolved_embedding = summarize_resolved(embedding, kind="embedding")
        self._configured = True

    @property
    def installed(self) -> bool:
        return bool(self.availability().version)

    @property
    def resolved_info(self) -> dict[str, Any]:
        return {"chat": self._resolved_chat, "embedding": self._resolved_embedding}

    def validate_configuration(self, config: CogneeConfig) -> None:
        validate_models(config)

    async def initialize(self) -> CogneeAvailability:
        async with self._initialize_lock:
            if self._closing:
                raise RuntimeError("Cognee 客户端正在关闭")
            if not self.config.enabled:
                return self.availability("未启用")
            if not self.installed:
                return self.availability("缺少 cognee 依赖，请执行 uv sync")
            try:
                await self._configure()
                self._import_error, self._configuration_required = "", False
            except CogneeConfigError as exc:
                self._configured, self._configuration_required = False, True
                self._import_error = str(exc)
            except Exception as exc:
                self._configured, self._configuration_required = False, False
                self._import_error = str(exc)
                log(f"Cognee 模型配置解析失败: {exc}", "WARNING")
            return self.availability()

    async def available_datasets(self, names: list[str]) -> list[str]:
        async with self._datasets_lock:
            if time.monotonic() >= self._datasets_until:
                rows = await self.list_datasets(wait_for_index=False)
                self._dataset_names = {row["name"] for row in rows}
                self._datasets_until = time.monotonic() + 30
        return [name for name in names if name in self._dataset_names]

    def global_context_ready(self, dataset: str) -> bool:
        return dataset in self._summary_datasets

    def dataset_catalog(self) -> DatasetCatalog:
        """只读观测面，不等待索引锁、不启动 SDK；在途写入不发布到目录。"""
        return DatasetCatalog(items=[row.copy() for row in self._catalog], ready=self._catalog_at > 0,
                              busy=self._operation_lock.locked(), updated_at=self._catalog_at,
                              error=self._fault or self._import_error)

    def _remember_catalog(self, datasets: list[DatasetRecord]) -> None:
        previous = {row["id"]: row for row in self._catalog}
        self._catalog = [{**previous.get(row["id"], row), **row} for row in datasets]
        self._catalog_at = time.time()

    def _remember_manifest(self, manifest: IndexManifest) -> None:
        """数量来自已提交清单；页面轮询不逐库扫描，也不显示未提交的候选文档。"""
        self._remember_catalog([DatasetRecord(
            id=identity, name=row.name, document_count=len(row.documents),
            processed_count=sum(document.processed for document in row.documents.values()),
        ) for identity, row in manifest.datasets.items()])

    def _inside_transaction(self) -> bool:
        owner = self._transaction.get()
        if owner is not None and owner is not asyncio.current_task():
            raise RuntimeError("索引事务不能由派生任务绕过互斥锁调用")
        return owner is not None

    def availability(self, reason: str = "") -> CogneeAvailability:
        try:
            version = importlib.metadata.version("cognee")
        except importlib.metadata.PackageNotFoundError:
            version = ""
        return CogneeAvailability(installed=bool(version), enabled=self.config.enabled,
            ready=bool(self.config.enabled and self._configured and not self._fault),
            configuration_required=self._configuration_required, version=version,
            reason=reason or self._fault or self._import_error)

    async def _worker(self) -> CogneeProcess:
        if self._closing:
            raise RuntimeError("Cognee 正在关闭")
        if self._fault:
            raise RuntimeError(self._fault)
        if self._process and self._worker_revision != self._revision:
            await self._stop(clean=True)
        root = Path(self.config.absolute_data_root).resolve()
        if self._checkpoints is None or self._checkpoints.root != root:
            await self._stop()
            if self._owner:
                self._owner.release()
            self._owner, self._checkpoints = None, None
            checkpoints = IndexCheckpoints(root)
            checkpoints.control.mkdir(parents=True, exist_ok=True)
            owner = FileLock(str(checkpoints.control / "owner.lock"), timeout=0)
            owner.acquire()
            try:
                self._phase = "recovering"
                await _disk_job(checkpoints.load)
                manifest = await _disk_job(checkpoints.read_manifest)
                if manifest is not None:
                    self._remember_manifest(IndexManifest.model_validate(manifest))
            except BaseException:
                owner.release()
                raise
            self._owner, self._checkpoints = owner, checkpoints
            self._needs_recovery = False
        if self._process is None:
            if self._needs_recovery:
                await self._recover()
            await _disk_job(self._checkpoints.mark_open)
            self._process = CogneeProcess(diagnostic_path=self._checkpoints.control / "worker.log",
                                          commit=self._checkpoints.state["committed"])
            try:
                await self._process.start(self.config.to_dict(), self._chat_payload, self._embedding_payload)
            except Exception as exc:
                self._fault = f"Cognee 工作进程初始化失败: {exc}"
                raise
            self._worker_revision = self._revision
        if not self._inside_transaction():
            self._phase = "idle"
        return self._process

    async def _stop(self, *, clean: bool = False) -> None:
        async with self._stop_lock:
            process = self._process
            if process:
                try:
                    await process.stop(clean=clean)
                finally:
                    if process.process is None:
                        self._process = None
                        self._finished_writes += process.written_bytes

    @asynccontextmanager
    async def idle_snapshot(self) -> AsyncIterator[None]:
        """卷备份/迁移的静默窗口，隔离召回并关闭工作进程后再读取文件。"""
        async with self._operation_lock:
            await self._stop()
            if self._checkpoints and self._checkpoints.state.get("dirty"):
                await self._recover()
            yield

    async def _recover(self) -> None:
        self._needs_recovery = True
        if self._checkpoints is None:
            return
        self._phase = "recovering"
        try:
            await _disk_job(self._checkpoints.restore)
        except Exception as exc:
            self._fault = f"Cognee 恢复尚未完成，禁止打开候选区: {exc}"
            self._phase = "failed"
            raise
        self._needs_recovery = False
        self._phase = "failed" if self._fault else "idle"

    async def _abort(self, error: BaseException) -> None:
        self._needs_recovery = True
        self._phase = "recovering"
        await self._stop()
        self._summary_datasets.clear()
        self._datasets_until = 0
        if isinstance(error, WorkerCrashed):
            self._fault = str(error) + "；已保留提交快照，请检查后点击重试或重建"
        if self._checkpoints and not self._closing:
            await self._recover()

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[None]:
        """批次所有派生写入一次发布，调用者仅在退出成功后提交原生同步映射。"""
        if self._inside_transaction():
            yield
            return
        availability = await self.initialize()
        if not availability.ready:
            raise RuntimeError(availability.reason or "Cognee 未就绪")
        async with self._operation_lock:
            token = self._transaction.set(asyncio.current_task())
            try:
                self._phase = "preparing"
                probe = await self._worker()
                assert self._checkpoints is not None
                previous = await _disk_job(self._checkpoints.read_manifest)
                baseline = IndexManifest.model_validate(previous) if previous is not None else await self._inspect(probe)
                if self._source_mappings is not None:
                    baseline.check_sources(await self._source_mappings())
                self._plan = MutationPlan(baseline)
                self._transaction_writes = False
                self._phase = "writing"
                yield
                if not self._transaction_writes:
                    return
                self._phase = "committing"
                candidate = await self._inspect(await self._worker())
                self._plan.validate(candidate)
                await self._stop(clean=True)
                reopened = await self._inspect(await self._worker())
                if reopened != candidate:
                    raise RuntimeError("索引关闭后重新打开验收不一致，候选区不提交")
                await self._stop(clean=True)
                checkpoints = self._checkpoints
                await _disk_job(lambda: checkpoints.commit(candidate.model_dump(mode="json")))
                self._remember_manifest(candidate)
            except BaseException as exc:
                await self._abort(exc)
                raise
            finally:
                self._transaction.reset(token)
                self._plan = None
                self._transaction_writes = False
                self._phase = "failed" if self._fault else "idle"

    async def _inspect(self, process: CogneeProcess) -> IndexManifest:
        names = ([projection_dataset(self.projection_namespace),
                  projection_dataset(self.projection_namespace, graph=True)] if self.projection_namespace else None)
        if names is not None and self._plan:
            names += [row.name for row in self._plan.added.values()]
        result = await process.call("inspect_index", names, timeout=self.config.pipeline_timeout_seconds)
        return IndexManifest.model_validate(result)

    async def _invoke(self, method: str, *args: Any, write: bool = False,
                      wait_for_index: bool = True, **kwargs: Any) -> Any:
        if write and not self._inside_transaction():
            async with self.write_transaction():
                return await self._invoke(method, *args, write=True, **kwargs)
        limit = self.config.pipeline_timeout_seconds if write else self.config.timeout_seconds
        if self._inside_transaction():
            # 图谱增强与版本维护不改变文档清单，也必须按实际写调用发布。
            previous_writes = self._transaction_writes
            self._transaction_writes |= write
            result = await (await self._worker()).call(method, *args, timeout=limit, **kwargs)
            if (method == "compact_vector_storage" and not result["errors"]
                    and result["executed_tasks"] == 0 and result["old_versions_removed"] == 0):
                self._transaction_writes = previous_writes
            return result
        availability = await self.initialize()
        if not availability.ready:
            raise RuntimeError(availability.reason or "Cognee 未就绪")
        # 在事务外拒绝排队读取候选区，避免模型构图耗尽整段召回预算。
        # 拒绝发生在读取/恢复异常处理之外，不中断或回滚持锁写事务。
        if not wait_for_index and self._phase in {"preparing", "writing", "committing", "recovering"}:
            raise CogneeIndexBusy("Cognee 索引正在写入或恢复，本次召回使用原生记忆；稍后可重试图谱检索")
        task = asyncio.create_task(self._read_call(method, args, kwargs, limit, wait_for_index),
                                   name=f"memory.cognee.read.{method}")
        self._reads.add(task)
        task.add_done_callback(self._read_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelling():
                task.cancel()
            raise

    def _read_done(self, task: asyncio.Task[Any]) -> None:
        self._reads.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log(f"Cognee 读取收束: {type(error).__name__}", "DEBUG", tag="记忆")

    async def _read_call(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any], limit: float,
                         wait_for_index: bool) -> Any:
        """客户端持有读取任务；取消后的回收仍持锁，调用方无需等待进程退出。"""
        if not wait_for_index and self._phase in {"preparing", "writing", "committing", "recovering"}:
            raise CogneeIndexBusy("Cognee 索引正在写入或恢复，请在索引空闲后重试")
        async with asyncio.timeout(limit):
            async with self._operation_lock:
                try:
                    return await (await self._worker()).call(method, *args, timeout=limit, **kwargs)
                except BaseException as exc:
                    await self._abort(exc)
                    raise

    def quiesce(self) -> None:
        self._closing = True

    async def close(self) -> None:
        self.quiesce()
        for task in self._reads:
            if not task.cancelling():
                task.cancel()
        # 工作区可丢弃，无需等待模型或原生线程。退出后由 dirty 记录驱动恢复。
        await self._stop()
        await asyncio.gather(*self._reads, return_exceptions=True)
        async with self._operation_lock:
            if self._owner:
                self._owner.release()
                self._owner = None
            self._configured = False

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict[str, Any],
                             dataset_name: str = "", node_set: list[str] | None = None) -> InputDocument:
        return make_document(data, label=label, external_metadata=external_metadata,
                             dataset_name=dataset_name, node_set=node_set)

    async def add(self, data: list[InputDocument], **kwargs: Any) -> IngestionResult:
        self._summary_datasets.clear()
        self._datasets_until = 0
        async with self.write_transaction():
            result = _INGESTION.validate_python(await self._invoke("add", data, write=True, **kwargs))
            assert self._plan is not None
            self._plan.add(result["dataset_id"], str(kwargs.get("dataset_name", "main_dataset")), data)
            return result

    async def cognify(self, **kwargs: Any) -> Any:
        return await self._invoke("cognify", write=True, **kwargs)

    async def improve(self, dataset: str = "main_dataset", **kwargs: Any) -> Any:
        async with self.write_transaction():
            result = await self._invoke("improve", dataset=dataset, write=True, **kwargs)
            if any(stage.get("status") in {"failed", "error"} for stage in result.get("stages", [])):
                raise RuntimeError("Cognee 增强未完成，候选索引未提交")
        if any(stage.get("stage") == "global_context_index" and stage.get("status") in
               {"completed", "already_completed"} for stage in result.get("stages", [])):
            self._summary_datasets.add(dataset)
        return result

    async def recall(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        if not self.projection_ready:
            return []
        return [CogneeRecallItem.model_validate(item) for item in
                await self._invoke("recall", query_text, wait_for_index=False, **kwargs)]

    async def list_datasets(self, *, wait_for_index: bool = True, **kwargs: Any) -> list[DatasetRecord]:
        rows = _DATASETS.validate_python(await self._invoke("list_datasets", wait_for_index=wait_for_index, **kwargs))
        if not self._inside_transaction():
            self._remember_catalog(rows)
        return rows

    async def list_data(self, dataset_id: str, **kwargs: Any) -> list[DocumentRecord]:
        return _DOCUMENTS.validate_python(await self._invoke("list_data", dataset_id, **kwargs))

    async def delete_data(self, dataset_id: str, data_id: str, **kwargs: Any) -> Any:
        self._summary_datasets.clear()
        self._datasets_until = 0
        async with self.write_transaction():
            result = await self._invoke("delete_data", dataset_id, data_id, write=True, **kwargs)
            assert self._plan is not None
            self._plan.deleted.setdefault(dataset_id, set()).add(data_id)
            return result

    async def prepare_deletions(self, targets: list[tuple[str, str]]) -> None:
        """仅在现有写事务中预取删除定位，缓存不跨候选事务。"""
        if not self._inside_transaction():
            raise RuntimeError("删除准备必须位于索引写事务内")
        await self._invoke("prepare_deletions", targets)

    async def delete_dataset(self, dataset_id: str) -> None:
        self._datasets_until = 0
        async with self.write_transaction():
            await self._invoke("delete_dataset", dataset_id, write=True)
            assert self._plan is not None
            self._plan.retired.add(dataset_id)

    async def verify_graph_documents(self, dataset_id: str, data_ids: list[str]) -> None:
        """发布事务合并到独立清单验收；独立读取仍即时检查原生图。"""
        if self._inside_transaction():
            assert self._plan is not None
            self._plan.require_processed(dataset_id, data_ids)
            if self._transaction_writes:
                return
        await self._invoke("verify_graph_documents", dataset_id, data_ids)

    async def compact_vector_storage(
        self, retention_days: float, *, rotation: int | None = None, prune_only: bool = False,
    ) -> dict[str, Any]:
        if rotation is not None:
            self._compact_rotation = rotation
        try:
            result = await self._invoke("compact_vector_storage", retention_days,
                                        rotation=self._compact_rotation, prune_only=prune_only, write=True)
        except BaseException:
            self._compact_rotation += 1
            raise
        self._compact_rotation = int(result["next_rotation"])
        return result

    async def pipeline_progress(self, dataset_id: str, documents: dict[str, bool] | None = None) -> CogneePipelineProgress:
        # 元数据观测允许与同一工作进程的长抽取并行，不另开图库连接。
        if self._process is None:
            return CogneePipelineProgress()
        value = await self._process.call("pipeline_progress", dataset_id, documents=documents, timeout=4)
        return CogneePipelineProgress.model_validate(value)

    def document_processed(self, item: DocumentRecord, dataset_id: str) -> bool:
        return document_processed(item, dataset_id)

    def search_type(self, name: str) -> str:
        return name.upper()
