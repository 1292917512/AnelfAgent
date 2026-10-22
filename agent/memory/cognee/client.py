"""宿主 Cognee 门面：进程隔离、索引提交与失败恢复。

模型看到什么：沿用既有记忆工具与召回结果，维护窗口退回原生检索。
token 影响：提交与恢复不调用模型；未提交批次可能重新抽取。
缓存影响：不改变提示词及稳定前缀。
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

from filelock import FileLock

from .checkpoints import IndexCheckpoints
from .config import CogneeConfig
from .embedding_identity import validate_embedding
from .llm_bridge import CogneeConfigError, resolve_chat_llm_config, resolve_embedding_llm_config, summarize_resolved
from .process import CogneeProcess, WorkerCrashed
from .sdk_client import SDKClient
from .types import CogneeAvailability, CogneePipelineProgress, CogneeRecallItem
from .wire import InputDocument, make_document

T = TypeVar("T")


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


class CogneeClient(SDKClient):
    """公共接口保持稳定；SDKClient 仅作为工作进程实现，不在宿主打开数据库。"""

    def __init__(self, config: CogneeConfig) -> None:
        super().__init__(config)
        self._operation_lock = asyncio.Lock()
        self._transaction: ContextVar[bool] = ContextVar("cognee_index_transaction", default=False)
        self._process: CogneeProcess | None = None
        self._checkpoints: IndexCheckpoints | None = None
        self._owner: FileLock | None = None
        self._fault = ""
        self._phase = "idle"
        self._revision = 0
        self._worker_revision = -1
        self._verifications: list[tuple[str, list[str]]] = []
        self._finished_writes = 0
        self._needs_recovery = False

    def sample_write_bytes(self) -> int:
        active = self._process.sample_writes() if self._process else 0
        return super().sample_write_bytes() + self._finished_writes + active

    def reconfigure(self, config: CogneeConfig) -> None:
        super().reconfigure(config)
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
        from agent.llm import get_llm_manager

        manager = get_llm_manager()
        chat = resolve_chat_llm_config(self.config.chat, manager)
        embedding = resolve_embedding_llm_config(self.config.embedding, manager)
        if not embedding:
            raise CogneeConfigError("尚未配置 Embedding 模型，请在模型管理中添加")
        embedding = validate_embedding(Path(self.config.absolute_data_root), embedding)
        if self._chat_payload != chat or self._embedding_payload != embedding:
            self._revision += 1
        self._chat_payload, self._embedding_payload = chat, embedding
        self._resolved_chat = summarize_resolved(chat, kind="chat")
        self._resolved_embedding = summarize_resolved(embedding, kind="embedding")
        self._configured = True

    def availability(self, reason: str = "") -> CogneeAvailability:
        try:
            version = importlib.metadata.version("cognee")
        except importlib.metadata.PackageNotFoundError:
            version = ""
        return CogneeAvailability(installed=self.installed, enabled=self.config.enabled,
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
            except BaseException:
                owner.release()
                raise
            self._owner, self._checkpoints = owner, checkpoints
            self._needs_recovery = False
        if self._process is None:
            if self._needs_recovery:
                await self._recover()
            await _disk_job(self._checkpoints.mark_open)
            self._process = CogneeProcess()
            try:
                await self._process.start(self.config.to_dict(), self._chat_payload, self._embedding_payload)
            except Exception as exc:
                self._fault = f"Cognee 工作进程初始化失败: {exc}"
                raise
            self._worker_revision = self._revision
        if not self._transaction.get():
            self._phase = "idle"
        return self._process

    async def _stop(self, *, clean: bool = False) -> None:
        process, self._process = self._process, None
        if process:
            try:
                await process.stop(clean=clean)
            finally:
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
        if self._transaction.get():
            yield
            return
        availability = await self.initialize()
        if not availability.ready:
            raise RuntimeError(availability.reason or "Cognee 未就绪")
        async with self._operation_lock:
            token = self._transaction.set(True)
            self._verifications = []
            try:
                self._phase = "preparing"
                await self._worker()
                self._phase = "writing"
                yield
                self._phase = "committing"
                # 同一数据集里已提交的旧文档也必须保留，不能只验收本轮新增文档。
                expected_by_dataset: dict[str, set[str]] = {}
                for dataset_id, data_ids in self._verifications:
                    expected_by_dataset.setdefault(dataset_id, set()).update(data_ids)
                probe = await self._worker()
                if expected_by_dataset:
                    present = {str(row["id"]) for row in await probe.call("list_datasets")}
                    for dataset_id, expected in expected_by_dataset.items():
                        if dataset_id not in present and not expected:
                            continue
                        rows = await probe.call("list_data", dataset_id)
                        expected.update(str(row["id"]) for row in rows if self.document_processed(row, dataset_id))
                        await probe.call("verify_graph_documents", dataset_id, sorted(expected),
                                         timeout=self.config.pipeline_timeout_seconds)
                self._verifications = [(key, sorted(value)) for key, value in expected_by_dataset.items() if value]
                await self._stop(clean=True)
                # checkpoint/close 也可能破坏图库；重新打开并验收后才发布文件快照。
                if self._verifications:
                    probe = await self._worker()
                    for dataset_id, data_ids in self._verifications:
                        await probe.call("verify_graph_documents", dataset_id, data_ids,
                                         timeout=self.config.pipeline_timeout_seconds)
                    await self._stop(clean=True)
                assert self._checkpoints is not None
                await _disk_job(self._checkpoints.commit)
            except BaseException as exc:
                await self._abort(exc)
                raise
            finally:
                self._transaction.reset(token)
                self._phase = "failed" if self._fault else "idle"

    async def _invoke(self, method: str, *args: Any, write: bool = False, **kwargs: Any) -> Any:
        if write and not self._transaction.get():
            async with self.write_transaction():
                return await self._invoke(method, *args, write=True, **kwargs)
        limit = self.config.pipeline_timeout_seconds if write else self.config.timeout_seconds
        if self._transaction.get():
            return await (await self._worker()).call(method, *args, timeout=limit, **kwargs)
        availability = await self.initialize()
        if not availability.ready:
            raise RuntimeError(availability.reason or "Cognee 未就绪")
        # 批次期间不读正在修改的图库；超时由既有召回层降级到原生结果。
        async with asyncio.timeout(limit):
            async with self._operation_lock:
                try:
                    return await (await self._worker()).call(method, *args, timeout=limit, **kwargs)
                except BaseException as exc:
                    await self._abort(exc)
                    raise

    def quiesce(self) -> None:
        super().quiesce()

    async def close(self) -> None:
        self.quiesce()
        # 工作区可丢弃，无需等待模型或原生线程。退出后由 dirty 记录驱动恢复。
        await self._stop()
        async with self._operation_lock:
            if self._owner:
                self._owner.release()
                self._owner = None
            self._configured = False

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict[str, Any],
                             dataset_name: str = "", node_set: list[str] | None = None) -> InputDocument:
        return make_document(data, label=label, external_metadata=external_metadata,
                             dataset_name=dataset_name, node_set=node_set)

    async def add(self, data: Any, **kwargs: Any) -> Any:
        self._summary_datasets.clear()
        self._datasets_until = 0
        return await self._invoke("add", data, write=True, **kwargs)

    async def cognify(self, **kwargs: Any) -> Any:
        return await self._invoke("cognify", write=True, **kwargs)

    async def improve(self, dataset: str = "main_dataset", **kwargs: Any) -> Any:
        async with self.write_transaction():
            result = await self._invoke("improve", dataset=dataset, write=True, **kwargs)
            if any(stage.get("status") in {"failed", "error"} for stage in result.get("stages", [])):
                raise RuntimeError("Cognee 增强未完成，候选索引未提交")
            for row in await self.list_datasets():
                if row["name"] == dataset:
                    self._verifications.append((str(row["id"]), []))
        if any(stage.get("stage") == "global_context_index" and stage.get("status") in
               {"completed", "already_completed"} for stage in result.get("stages", [])):
            self._summary_datasets.add(dataset)
        return result

    async def recall(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        if not self.projection_ready:
            return []
        return [CogneeRecallItem.model_validate(item) for item in await self._invoke("recall", query_text, **kwargs)]

    async def list_datasets(self, **kwargs: Any) -> Any:
        return await self._invoke("list_datasets", **kwargs)

    async def list_data(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._invoke("list_data", dataset_id, **kwargs)

    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        self._summary_datasets.clear()
        self._datasets_until = 0
        async with self.write_transaction():
            result = await self._invoke("delete_data", dataset_id, data_id, write=True, **kwargs)
            self._verifications.append((str(dataset_id), []))
            return result

    async def delete_dataset(self, dataset_id: str) -> None:
        self._datasets_until = 0
        await self._invoke("delete_dataset", dataset_id, write=True)

    async def verify_graph_documents(self, dataset_id: str, data_ids: list[str]) -> None:
        await self._invoke("verify_graph_documents", dataset_id, data_ids)
        if self._transaction.get():
            self._verifications.append((dataset_id, list(data_ids)))

    async def compact_vector_storage(self, retention_days: float, *, rotation: int | None = None) -> dict[str, Any]:
        if rotation is not None:
            self._compact_rotation = rotation
        try:
            return await self._invoke("compact_vector_storage", retention_days,
                                      rotation=self._compact_rotation, write=True)
        finally:
            self._compact_rotation += 1

    async def pipeline_progress(self, dataset_id: str) -> CogneePipelineProgress:
        # 元数据观测允许与同一工作进程的长抽取并行，不另开图库连接。
        if self._process is None:
            return CogneePipelineProgress()
        value = await self._process.call("pipeline_progress", dataset_id, timeout=4)
        return CogneePipelineProgress.model_validate(value)

    def document_processed(self, item: Any, dataset_id: str) -> bool:
        status = (item.get("pipeline_status") or {}).get("cognify_pipeline", {}).get(dataset_id)
        if isinstance(status, dict):
            status = status.get("status")
        return status == "DATA_ITEM_PROCESSING_COMPLETED"

    def search_type(self, name: str) -> str:
        return name.upper()
