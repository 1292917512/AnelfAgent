"""仅在工作进程装配的 Cognee 适配器：SDK 类型、配置和资源释放。"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import UUID

from core.log import log

from . import adapters, ladybug_guard
from .config import CogneeConfig
from .deletion import DeletionReads
from .embedding_identity import validate_embedding
from .llm_bridge import (
    CogneeConfigError,
    anthropic_env_bridge,
    summarize_resolved,
)
from .model_config import resolve_models, validate_models
from .sdk_contract import close_engines, validate_sdk
from .types import CogneeAvailability, CogneePipelineError, CogneePipelineProgress, CogneeRecallItem
from .wire import InputDocument, make_document


class SDKClient:
    """隔离 Cognee 导入、配置和返回类型的异步门面。"""

    def __init__(self, config: CogneeConfig) -> None:
        self.config = config.normalized()
        self._module: Optional[Any] = None
        self._closing = False
        self._calls: set[asyncio.Future[Any]] = set()
        self._configured = False
        self._import_error = ""
        self._configuration_required = False
        self._initialize_lock = asyncio.Lock()
        self._recall_slots = asyncio.Semaphore(4)
        self._datasets_lock = asyncio.Lock()
        self._dataset_names: set[str] = set()
        self._datasets_until = 0.0
        self._compact_rotation = 0
        self._resolved_chat: dict[str, Any] = {}
        self._resolved_embedding: dict[str, Any] = {}
        self._embedding_payload: dict[str, Any] = {}
        self._chat_payload: dict[str, Any] = {}
        self._summary_datasets: set[str] = set()
        self._deletion_reads: DeletionReads | None = None
        self.projection_ready = True
        self.projection_namespace = ""

    @property
    def installed(self) -> bool:
        try:
            return importlib.util.find_spec("cognee") is not None
        except (ImportError, ValueError):
            return False

    def sample_write_bytes(self) -> int:
        import psutil

        return int(psutil.Process().io_counters().write_bytes)

    @property
    def resolved_info(self) -> dict[str, Any]:
        """已解析的模型配置摘要（脱敏），供状态接口展示。"""
        return {
            "chat": self._resolved_chat,
            "embedding": self._resolved_embedding,
        }

    def reconfigure(self, config: CogneeConfig) -> None:
        """热更新配置：下次调用时按新配置重新映射模型。

        重置配置标记后由 initialize 串行重新映射；原生 LLM 适配器按调用读取
        当前配置，带缓存的 Instructor 与 embedding 引擎使用配置内容作为键。
        """
        self._deletion_reads = None
        self.config = config.normalized()
        self._configured = False
        self._import_error = ""
        self._configuration_required = False
        self._resolved_chat = {}
        self._resolved_embedding = {}
        self._datasets_until = 0.0
        self._summary_datasets.clear()

    async def initialize(self) -> CogneeAvailability:
        """串行解析并应用配置，并发调用复用已就绪的客户端。"""
        async with self._initialize_lock:
            if self._closing:
                raise RuntimeError("Cognee 客户端正在关闭")
            return await self._initialize()

    def quiesce(self) -> None:
        """封闭 SDK 调用入口并取消抽取、召回协程，原生关闭由 close 监督。"""
        if self._closing:
            return
        self._closing = True
        for task in self._calls:
            if not task.done():
                task.cancel()

    async def close(self) -> None:
        """宿主停止调用后释放 SDK 缓存的引擎，等待数据库线程与文件句柄关闭。

        Cognee 使用进程级引擎缓存；宿主只装配一个客户端，关闭由生命周期
        在召回与投影工作停止后统一调用。关闭不删除数据或改变投影状态。
        """
        self.quiesce()
        self._deletion_reads = None
        await asyncio.gather(*self._calls, return_exceptions=True)
        if self._module is None:
            return
        async with self._initialize_lock:
            try:
                await close_engines()
            finally:
                self._configured = False

    def validate_configuration(self, config: CogneeConfig) -> None:
        """保存前检查显式抽取配置和向量空间，不调用模型或打开数据库。"""
        validate_models(config)

    async def _initialize(self) -> CogneeAvailability:
        if not self.config.enabled:
            return self.availability(reason="未启用")
        if not self.installed:
            return self.availability(reason="缺少 cognee 依赖，请在项目目录执行 uv sync")
        try:
            await self._configure()
            self._import_error = ""
            self._configuration_required = False
            return self.availability()
        except CogneeConfigError as exc:
            self._configured = False
            self._import_error = str(exc)
            self._configuration_required = True
            return self.availability()
        except Exception as exc:
            self._configured = False
            self._import_error = str(exc)
            self._configuration_required = False
            log(f"Cognee 初始化失败，保持原记忆后端: {exc}", "WARNING")
            return self.availability(reason=self._import_error)

    def availability(self, reason: str = "") -> CogneeAvailability:
        version = str(getattr(self._module, "__version__", "")) if self._module else ""
        ready = bool(self.config.enabled and self._module is not None and self._configured)
        return CogneeAvailability(
            installed=self.installed,
            enabled=self.config.enabled,
            ready=ready,
            configuration_required=self.config.enabled and self._configuration_required,
            version=version,
            reason=reason or ("" if ready else self._import_error),
        )

    def _import_cognee(self) -> Any:
        if self._module is not None:
            return self._module
        # 外层工作进程统一拥有 SDK、图与向量引擎，避免 SDK 再创建未受宿主管理的子进程。
        os.environ.setdefault("GRAPH_DATABASE_SUBPROCESS_ENABLED", "false")
        os.environ.setdefault("VECTOR_DB_SUBPROCESS_ENABLED", "false")
        # Cognee 导入时会 dotenv.load_dotenv(override=True)。恢复环境，避免污染宿主进程。
        original_env = dict(os.environ)
        try:
            # 抑制 cognee 的 structlog 喧哗：只输出 WARNING 及以上
            os.environ.setdefault("LOG_LEVEL", "WARNING")
            os.environ.setdefault("COGNEE_LOG_FILE", "false")
            self._module = importlib.import_module("cognee")
        finally:
            os.environ.clear()
            os.environ.update(original_env)
        # cognee 的 setup_logging() 接管了 stdlib root logger，
        # 限制其 handler 级别以避免 pipeline 状态持续刷屏
        _quieten_cognee_logger()
        try:
            validate_sdk(self._module)
            ladybug_guard.install(lambda: self.config)
            adapters.install()
        except Exception:
            self._module = None
            raise
        return self._module

    async def _configure(self) -> None:
        while self.config.enabled:
            config = self.config
            chat_payload, embedding_payload = resolve_models(config)
            self._resolved_chat = summarize_resolved(chat_payload, kind="chat")
            self._resolved_embedding = summarize_resolved(embedding_payload, kind="embedding")

            if self._configured and self._chat_payload == chat_payload and self._embedding_payload == embedding_payload:
                return
            self._configured = False
            module = await asyncio.to_thread(self._import_cognee)
            if config is not self.config:
                continue
            root = Path(config.absolute_data_root)
            root.mkdir(parents=True, exist_ok=True)
            module.config.system_root_directory(str(root / "system"))
            module.config.data_root_directory(str(root / "data"))
            anthropic_env_bridge(chat_payload)
            module.config.set_llm_config(chat_payload)
            module.config.set_embedding_config(embedding_payload)
            self._chat_payload = chat_payload
            self._embedding_payload = embedding_payload
            if (root / "system").exists():
                validate_embedding(root, embedding_payload, record=True)
            self._configured = True
            return

    async def configure_resolved(self, chat: dict[str, Any], embedding: dict[str, Any]) -> None:
        """工作进程应用宿主已解析的配置，不创建第二份模型管理器。"""
        # 原生 DLL 在子进程主线程首次加载；无需让宿主承担导入阻塞。
        module = self._import_cognee()
        root = Path(self.config.absolute_data_root)
        embedding = validate_embedding(root, embedding)
        module.config.system_root_directory(str(root / "system"))
        module.config.data_root_directory(str(root / "data"))
        anthropic_env_bridge(chat)
        module.config.set_llm_config(chat)
        module.config.set_embedding_config(embedding)
        self._chat_payload, self._embedding_payload = chat, embedding
        self._configured = True

    async def _call(
        self,
        dotted_name: str,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        if self._closing:
            raise RuntimeError("Cognee 客户端正在关闭")
        module = self._module
        if module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready or self._module is None:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
            module = self._module
        if self._closing:
            raise RuntimeError("Cognee 客户端正在关闭")
        target: Any = module
        for part in dotted_name.split("."):
            target = getattr(target, part)
        result = target(*args, **kwargs)
        if hasattr(result, "__await__"):
            limit = timeout if timeout is not None else self.config.timeout_seconds
            deadline = asyncio.timeout(limit)
            task = asyncio.ensure_future(result)
            self._calls.add(task)
            try:
                async with deadline:
                    return await task
            except TimeoutError as exc:
                if deadline.expired():
                    raise TimeoutError(f"Cognee 调用 {dotted_name} 总时限已到（{limit:g}s）") from exc
                detail = str(exc).strip() or "底层未提供错误详情"
                raise TimeoutError(
                    f"Cognee 调用 {dotted_name} 内部步骤超时（未达到 {limit:g}s 总时限）: {detail}"
                ) from exc
            finally:
                self._calls.discard(task)
        return result

    # v2 memory-oriented API

    async def recall(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        if not self.projection_ready:
            return []
        async with self._recall_slots:
            raw = await self._call("recall", query_text, **kwargs)
        return _normalize_recall(raw)

    async def available_datasets(self, names: list[str]) -> list[str]:
        """短缓存数据集目录，避开不存在的数据集；目录读取失败不误判为空。"""
        async with self._datasets_lock:
            if time.monotonic() >= self._datasets_until:
                try:
                    rows = await self.list_datasets()
                except Exception as exc:
                    log(f"Cognee 数据集目录读取失败，按原范围检索: {exc}", "DEBUG")
                    return names
                self._dataset_names = {
                    str(row.get("name", "") if isinstance(row, dict) else getattr(row, "name", ""))
                    for row in rows
                }
                self._datasets_until = time.monotonic() + 30
        return [name for name in names if name in self._dataset_names]

    def global_context_ready(self, dataset: str) -> bool:
        """只使用当前进程已验收且之后未发生写入的摘要；重启保守退回事实检索。"""
        return dataset in self._summary_datasets

    async def improve(self, dataset: str = "main_dataset", **kwargs: Any) -> Any:
        self._deletion_reads = None
        result = await self._call("improve", dataset=dataset,
                                  timeout=self.config.pipeline_timeout_seconds, **kwargs)
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        stages = value.get("stages", []) if isinstance(value, dict) else []
        if any(stage.get("stage") == "global_context_index" and stage.get("status") in
               {"completed", "already_completed"} for stage in stages):
            self._summary_datasets.add(dataset)
        return result

    # v1/lower-level public API
    async def add(self, data: Any, **kwargs: Any) -> Any:
        self._deletion_reads = None
        self._summary_datasets.clear()
        from cognee.tasks.ingestion.data_item import DataItem

        def sdk_input(item: Any) -> Any:
            if isinstance(item, InputDocument):
                return DataItem(item.data, literal_text=item.literal_text, label=item.label,
                                external_metadata=item.external_metadata, node_set=item.node_set,
                                data_id=UUID(item.data_id))
            return item

        data = [sdk_input(item) for item in data] if isinstance(data, list) else sdk_input(data)
        result = await self._call(
            "add",
            data,
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )
        value = result if isinstance(result, dict) else vars(result) if hasattr(result, "__dict__") else {}
        status = value.get("status", "")
        if status == "PipelineRunErrored":
            raise CogneePipelineError(
                "add", value.get("error_class", "") or "",
                value.get("error_message", "") or "摄入未完成",
            )
        self._datasets_until = 0.0
        if self._embedding_payload:
            from cognee.infrastructure.databases.vector.embeddings.config import get_embedding_context_config

            identity = dict(self._embedding_payload)
            detected = get_embedding_context_config().embedding_dimensions
            if detected:
                identity["embedding_dimensions"] = detected
            identity = validate_embedding(Path(self.config.absolute_data_root), identity, record=True)
            # add 的自动探测发生在 worker 上下文；公开配置也需更新，供其他召回协程使用。
            if identity != self._embedding_payload and self._module is not None:
                self._module.config.set_embedding_config(identity)
                self._embedding_payload = identity
                self._resolved_embedding = summarize_resolved(identity, kind="embedding")
        return result

    async def make_data_item(
        self,
        data: str,
        *,
        label: str,
        external_metadata: dict[str, Any],
        dataset_name: str = "",
        node_set: list[str] | None = None,
    ) -> InputDocument:
        """稳定 ID 由项目文档契约生成，SDK 转换仅发生在 add 边界。"""
        return make_document(data, label=label, external_metadata=external_metadata,
                             dataset_name=dataset_name, node_set=node_set)

    async def cognify(self, **kwargs: Any) -> Any:
        self._deletion_reads = None
        return await self._call(
            "cognify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )


    # Dataset namespace
    async def pipeline_progress(self, dataset_id: str, documents: dict[str, bool] | None = None) -> CogneePipelineProgress:
        """只读公开流水线进度；短超时避免观测接口阻塞管理页面。"""
        identity = _to_uuid(dataset_id)
        result = CogneePipelineProgress()
        errors: list[str] = []
        if documents is not None:
            from .sdk_progress import document_progress

            try:
                async with asyncio.timeout(1.5):
                    result.documents = await document_progress(str(identity), documents)
            except Exception as exc:
                errors.append(f"documents: {type(exc).__name__}: {exc}")
        try:
            async with asyncio.timeout(1):
                rows = await self._call("datasets.get_progress", [identity],
                                        pipeline_names=["cognify_pipeline"], timeout=1.0)
            row = rows.get(str(identity), {})
            progress = row.get("progress") or {}
            result.status = row.get("status", "")
            result.completed_items = progress.get("completed_items")
            result.total_items = progress.get("total_items")
            result.current_stage = progress.get("current_stage") or ""
        except Exception as exc:
            errors.append(f"pipeline: {type(exc).__name__}: {exc}")
        result.observation_error = "; ".join(errors)
        return result

    async def list_datasets(self, **kwargs: Any) -> Any:
        try:
            return await self._call("datasets.list_datasets", **kwargs)
        except Exception as exc:
            if _is_database_not_created(exc):
                # 重建清场后、进程重启前的中间态：引擎句柄指向已 prune 的
                # 元数据库，此时数据集确为空，降级返回避免 UI/工具层 500
                return []
            raise


    async def list_data(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._call("datasets.list_data", _to_uuid(dataset_id), **kwargs)

    async def delete_dataset(self, dataset_id: str) -> None:
        self._deletion_reads = None
        """经 SDK 删除指定数据集及其索引，保留仍被其他数据集引用的原文。"""
        await self._call("datasets.empty_dataset", _to_uuid(dataset_id), timeout=self.config.pipeline_timeout_seconds)
        self._datasets_until = 0

    def document_processed(self, item: Any, dataset_id: str) -> bool:
        """逐文档检查抽取完成标记；只有摄入元数据不能证明索引已生成。"""
        from cognee.modules.pipelines.models.DataItemStatus import is_data_item_completed

        status = item.get("pipeline_status") if isinstance(item, dict) else getattr(item, "pipeline_status", None)
        return bool(is_data_item_completed(((status or {}).get("cognify_pipeline") or {}).get(dataset_id)))


    async def prepare_deletions(self, targets: list[tuple[str, str]]) -> None:
        """在无并发增写的删除窗口中预取目标归属；失败时不保留部分准备缓存。"""
        self._deletion_reads = None
        reads = DeletionReads()
        await reads.prepare(targets)
        self._deletion_reads = reads

    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        self._summary_datasets.clear()
        self._datasets_until = 0.0
        if self._deletion_reads is None:
            return await self._call("datasets.delete_data", _to_uuid(dataset_id), _to_uuid(data_id), **kwargs)
        with self._deletion_reads.scope():
            return await self._call("datasets.delete_data", _to_uuid(dataset_id), _to_uuid(data_id), **kwargs)


    async def verify_graph_documents(self, dataset_id: str, data_ids: list[str]) -> None:
        """逐文档检查实际图库，拒绝仅有元数据完成标记的残缺索引。"""
        from .graph_validation import verify_graph_documents

        datasets = await self.list_datasets()
        if not any(str(item.get("id") if isinstance(item, dict) else item.id) == dataset_id
                   for item in datasets):
            raise RuntimeError(f"无法访问待验收的数据集: {dataset_id}")
        await asyncio.wait_for(verify_graph_documents(UUID(dataset_id), data_ids),
                               timeout=self.config.pipeline_timeout_seconds)

    async def inspect_index(self, graph_datasets: list[str] | None = None) -> dict[str, Any]:
        """一次 RPC 返回完整内容清单，仅打开当前代际或明确指定的图库。"""
        from .graph_validation import graph_document_stamps
        from .manifest import DatasetStamp, DocumentStamp, IndexManifest, document_identity
        from .wire import encode

        inventory = IndexManifest()
        digests: dict[str, str] = {}
        for row in await self.list_datasets():
            dataset = encode(row)
            dataset_id, name = str(dataset["id"]), str(dataset["name"])
            target = DatasetStamp(name=name)
            items = await self.list_data(dataset_id)
            documents = [encode(item) for item in items]
            locations = dict.fromkeys(str(document.get("raw_data_location") or "") for document in documents)
            pending = [location for location in locations if location not in digests]
            if pending:
                digests.update(await asyncio.to_thread(
                    _source_digests, Path(self.config.absolute_data_root) / "data", pending,
                ))
            for item, document in zip(items, documents, strict=True):
                metadata = document.get("external_metadata") or {}
                location = str(document.get("raw_data_location") or "")
                target.documents[str(document["id"])] = DocumentStamp(
                    identity=document_identity(document.get("label") or "", metadata, document.get("node_set")),
                    content_hash=str(metadata.get("anelf_content_hash", "")),
                    raw_hash=str(document.get("raw_content_hash") or document.get("content_hash") or ""),
                    source_digest=digests[location],
                    processed=self.document_processed(item, dataset_id),
                )
            if graph_datasets is None or name in graph_datasets:
                stamps = await graph_document_stamps(UUID(dataset_id),
                    [key for key, value in target.documents.items() if value.processed])
                for key, digest in stamps.items():
                    target.documents[key].chunks = digest
            inventory.datasets[dataset_id] = target
        return inventory.model_dump(mode="json")

    # Maintenance / diagnostics / visualization


    async def compact_vector_storage(
        self, retention_days: float, *, rotation: int | None = None, prune_only: bool = False,
    ) -> dict[str, Any]:
        """按单轮预算压缩 LanceDB 向量表并清理过期版本，回收磁盘空间。

        实现见 `storage.compact_lance_tree`；最新版本永远保留，逻辑数据不受影响。
        须在无并发写入时调用（协调器批次间窗口）。
        """
        self._deletion_reads = None
        if not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        from .storage import compact_lance_tree

        if rotation is not None:
            self._compact_rotation = rotation
        task = asyncio.create_task(asyncio.to_thread(
            compact_lance_tree,
            Path(self.config.absolute_data_root) / "system" / "databases",
            retention_days,
            rotation=self._compact_rotation,
            prune_only=prune_only,
            max_versions=self.config.compact_max_versions_per_run,
        ), name="memory.cognee.compact")
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # 原生写线程不可被协程取消；提交完成前不能释放宿主互斥锁。
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise
        finally:
            self._compact_rotation += 1


    def search_type(self, name: str) -> Any:
        if self._module is None:
            raise RuntimeError("Cognee 未初始化")
        return getattr(self._module.SearchType, name.upper())


def _source_digests(root: Path, locations: list[str]) -> dict[str, str]:
    """单次线程调度完整读取本批原文；缓存只在一次验收内共享，重新打开必须重新读。"""
    return {location: _source_digest(root, location) for location in locations}


def _source_digest(root: Path, location: str) -> str:
    """完整读取受管原文，防止元数据仍在但实际原文丢失或被截断。"""
    uri = urlsplit(location)
    if uri.scheme != "file" or uri.netloc not in {"", "localhost"} or uri.query or uri.fragment:
        raise RuntimeError("索引原文没有可核验的本地位置")
    path = Path(url2pathname(uri.path)).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("索引原文越出受管数据目录")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _to_uuid(value: Any) -> Any:
    """将字符串形式的 UUID 还原为 UUID 对象。

    cognee 的授权检查把 str 类型 dataset_id 当作数据集「名称」解析，
    直接传字符串 UUID 会被误判为无权访问（401），因此必须在边界处转换。
    """
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return UUID(text)
            except ValueError:
                return value
    return value


def _is_database_not_created(exc: BaseException) -> bool:
    """识别 cognee 元数据库未初始化（按类名匹配，cognee 为可选依赖不直接 import）。"""
    return type(exc).__name__ == "DatabaseNotCreatedError"


def _quieten_cognee_logger() -> None:
    """cognee 的 setup_logging() 接管了 stdlib root logger，限制其输出级别。"""
    import logging as _logging

    root = _logging.getLogger()
    for handler in root.handlers:
        if handler.level < _logging.WARNING:
            handler.setLevel(_logging.WARNING)
    # cognee 子 logger 也限制
    _logging.getLogger("cognee").setLevel(_logging.WARNING)


def _normalize_recall(raw_results: Any) -> list[CogneeRecallItem]:
    if raw_results is None:
        return []
    values = raw_results if isinstance(raw_results, list) else [raw_results]
    normalized: list[CogneeRecallItem] = []
    for index, item in enumerate(values):
        if hasattr(item, "model_dump"):
            data = item.model_dump(mode="python")
        elif isinstance(item, dict):
            data = dict(item)
        else:
            data = {"text": str(item)}

        source_value = str(data.get("source", "graph"))
        content = (
            data.get("text")
            or data.get("content")
            or data.get("answer")
            or data.get("context")
            or data.get("result")
            or ""
        )
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            continue
        metadata = dict(data["metadata"]) if isinstance(data.get("metadata"), dict) else {}
        raw = data.get("raw")
        external = raw.get("external_metadata") if isinstance(raw, dict) else None
        if isinstance(external, str):
            try:
                external = json.loads(external)
            except ValueError:
                log("Cognee 分块 external_metadata 不是有效 JSON，保留正文并标记未知归属", "DEBUG", tag="记忆")
                external = None
        if isinstance(external, dict):
            metadata.update(external)
        for field in ("conflicts", "references"):
            value = data.get(field) or (raw.get(field) if isinstance(raw, dict) else None)
            if value:
                metadata[field] = value
        if data.get("system_prompt"):
            from .evidence import graph_evidence_text

            content = graph_evidence_text(metadata.get("evidence"))
            if not content:
                continue
        if metadata.get("evidence"):
            metadata.setdefault("references", metadata["evidence"])
        raw_id = metadata.get("chunk_id") or data.get("id") or data.get("qa_id") or f"result-{index}"
        score_value = data.get("score", 0.0)
        score = float(score_value) if isinstance(score_value, (int, float)) else 0.0
        normalized.append(
            CogneeRecallItem(
                id=f"cognee:{raw_id}",
                content=content,
                score=score,
                source="cognee_chunk"
                if "chunk" in source_value.lower() or metadata.get("chunk_id") or data.get("kind") == "chunk"
                else "cognee_graph",
                dataset_id=str(data.get("dataset_id", "")),
                dataset_name=str(data.get("dataset_name", "")),
                metadata=metadata,
                raw=data,
            )
        )
    return normalized
