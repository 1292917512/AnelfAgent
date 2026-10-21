"""Cognee 公共 API 的懒加载门面与宿主配置边界。"""

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
from uuid import NAMESPACE_URL, UUID, uuid5

from core.log import log

from . import adapters, ladybug_guard
from .config import CogneeConfig
from .embedding_identity import validate_embedding
from .llm_bridge import (
    CogneeConfigError,
    anthropic_env_bridge,
    resolve_chat_llm_config,
    resolve_embedding_llm_config,
    summarize_resolved,
)
from .sdk_contract import close_engines, validate_sdk
from .types import CogneeAvailability, CogneePipelineError, CogneeRecallItem


class CogneeClient:
    """隔离 Cognee 导入、配置和返回类型的异步门面。"""

    def __init__(self, config: CogneeConfig) -> None:
        self.config = config.normalized()
        self._module: Optional[Any] = None
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
        self.projection_ready = True
        self.projection_namespace = ""

    @property
    def installed(self) -> bool:
        try:
            return importlib.util.find_spec("cognee") is not None
        except (ImportError, ValueError):
            return False

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
            return await self._initialize()

    async def close(self) -> None:
        """宿主停止调用后释放 SDK 缓存的引擎，等待数据库线程与文件句柄关闭。

        Cognee 使用进程级引擎缓存；宿主只装配一个客户端，关闭由生命周期
        在召回与投影工作停止后统一调用。关闭不删除数据或改变投影状态。
        """
        if self._module is None:
            return
        async with self._initialize_lock:
            try:
                await close_engines()
            finally:
                self._configured = False

    def validate_configuration(self, config: CogneeConfig) -> None:
        """保存前检查显式抽取配置和向量空间，不调用模型或打开数据库。"""
        if not config.enabled:
            return
        from agent.llm import get_llm_manager
        from agent.llm.llm_client import ModelType

        manager = get_llm_manager()
        if config.chat.source != "auto" or manager.get_all_by_type(ModelType.CHAT):
            resolve_chat_llm_config(config.chat, manager)
        payload = resolve_embedding_llm_config(config.embedding, manager)
        if payload:
            validate_embedding(Path(config.absolute_data_root), payload)

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
        # cognee 默认把 graph/vector 引擎放在子进程运行（pydantic-settings 按
        # 字段名读同名环境变量），每轮流水线都会创建 worker 子进程，父进程的
        # PIPE/信号量 fd 随运行时间单调泄露直至 EMFILE。宿主为单进程架构，
        # 改回进程内引擎（setdefault 保留环境变量覆盖）。
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
            try:
                from agent.llm import get_llm_manager

                manager = get_llm_manager()
                chat_payload = resolve_chat_llm_config(config.chat, manager)
                self._resolved_chat = summarize_resolved(chat_payload, kind="chat")
                embedding_payload = resolve_embedding_llm_config(config.embedding, manager)
                if embedding_payload is None:
                    raise CogneeConfigError(
                        "尚未配置 Embedding 模型，请在 Web 模型管理中添加，"
                        "或在 Cognee 向量化模型配置中指定"
                    )
                embedding_payload = validate_embedding(Path(config.absolute_data_root), embedding_payload)
                self._resolved_embedding = summarize_resolved(embedding_payload, kind="embedding")
            except CogneeConfigError:
                raise
            except Exception as exc:
                raise RuntimeError(f"无法映射 AnelfAgent 模型配置: {exc}") from exc

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

    async def _call(
        self,
        dotted_name: str,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        module = self._module
        if module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready or self._module is None:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
            module = self._module
        target: Any = module
        for part in dotted_name.split("."):
            target = getattr(target, part)
        result = target(*args, **kwargs)
        if hasattr(result, "__await__"):
            limit = timeout if timeout is not None else self.config.timeout_seconds
            deadline = asyncio.timeout(limit)
            try:
                async with deadline:
                    return await asyncio.ensure_future(result)
            except TimeoutError as exc:
                if deadline.expired():
                    raise TimeoutError(f"Cognee 调用 {dotted_name} 总时限已到（{limit:g}s）") from exc
                detail = str(exc).strip() or "底层未提供错误详情"
                raise TimeoutError(
                    f"Cognee 调用 {dotted_name} 内部步骤超时（未达到 {limit:g}s 总时限）: {detail}"
                ) from exc
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
        self._summary_datasets.clear()
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
    ) -> Any:
        """构造 Cognee 文档化公开输入类型 DataItem。"""
        if self._module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        data_item_module = importlib.import_module("cognee.tasks.ingestion.data_item")
        identity = json.dumps(
            [data, external_metadata, sorted(set(node_set or []))],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return data_item_module.DataItem(
            data,
            literal_text=True,
            label=label,
            external_metadata=external_metadata,
            node_set=sorted(set(node_set or [])),
            data_id=uuid5(NAMESPACE_URL, f"anelf:{dataset_name}:{label}:{hashlib.sha256(identity.encode()).hexdigest()}"),
        )

    async def cognify(self, **kwargs: Any) -> Any:
        return await self._call(
            "cognify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )


    # Dataset namespace
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

    def document_processed(self, item: Any, dataset_id: str) -> bool:
        """逐文档检查抽取完成标记；只有摄入元数据不能证明索引已生成。"""
        from cognee.modules.pipelines.models.DataItemStatus import is_data_item_completed

        status = item.get("pipeline_status") if isinstance(item, dict) else getattr(item, "pipeline_status", None)
        return bool(is_data_item_completed(((status or {}).get("cognify_pipeline") or {}).get(dataset_id)))


    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        self._summary_datasets.clear()
        self._datasets_until = 0.0
        return await self._call(
            "datasets.delete_data",
            _to_uuid(dataset_id),
            _to_uuid(data_id),
            **kwargs,
        )


    # Maintenance / diagnostics / visualization


    async def compact_vector_storage(self, retention_days: float) -> dict[str, Any]:
        """按单轮预算压缩 LanceDB 向量表并清理过期版本，回收磁盘空间。

        实现见 `storage.compact_lance_tree`；最新版本永远保留，逻辑数据不受影响。
        须在无并发写入时调用（协调器空闲窗口）。
        """
        if not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        from .storage import compact_lance_tree

        task = asyncio.create_task(asyncio.to_thread(
            compact_lance_tree,
            Path(self.config.absolute_data_root) / "system" / "databases",
            retention_days,
            rotation=self._compact_rotation,
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
