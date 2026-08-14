"""Cognee v1.4 稳定公共 API 的懒加载门面。"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from core.log import log

from .config import CogneeConfig
from .llm_bridge import (
    anthropic_env_bridge,
    resolve_chat_llm_config,
    resolve_embedding_llm_config,
    summarize_resolved,
)
from .types import CogneeAvailability, CogneeRecallItem


class CogneeClient:
    """隔离 Cognee 导入、配置和返回类型的异步门面。"""

    def __init__(self, config: CogneeConfig) -> None:
        self.config = config.normalized()
        self._module: Optional[Any] = None
        self._configured = False
        self._import_error = ""
        self._resolved_chat: dict[str, Any] = {}
        self._resolved_embedding: dict[str, Any] = {}

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

        cognee 的 LLM/embedding 引擎按配置内容哈希缓存（get_llm_client），
        set_llm_config 就地更新单例后，新配置会产生新的缓存键并即时生效，
        因此无需显式重建引擎，重置 _configured 触发 _configure 重映射即可。
        """
        self.config = config.normalized()
        self._configured = False
        self._import_error = ""
        self._resolved_chat = {}
        self._resolved_embedding = {}

    async def initialize(self) -> CogneeAvailability:
        if not self.config.enabled:
            return self.availability(reason="未启用")
        if not self.installed:
            return self.availability(reason="未安装可选依赖 cognee")
        try:
            module = await asyncio.to_thread(self._import_cognee)
            await self._configure(module)
            return self.availability()
        except Exception as exc:
            self._import_error = str(exc)
            log(f"Cognee 初始化失败，保持原记忆后端: {exc}", "WARNING")
            return self.availability(reason=self._import_error)

    def availability(self, reason: str = "") -> CogneeAvailability:
        version = str(getattr(self._module, "__version__", "")) if self._module else ""
        ready = bool(self.config.enabled and self._module is not None and self._configured)
        return CogneeAvailability(
            installed=self.installed,
            enabled=self.config.enabled,
            ready=ready,
            version=version,
            reason=reason or ("" if ready else self._import_error),
        )

    def _import_cognee(self) -> Any:
        if self._module is not None:
            return self._module
        # cognee 1.3 默认把 graph/vector 引擎放在子进程运行，每轮流水线都会
        # 创建 worker 子进程，父进程的 PIPE/信号量 fd 随运行时间单调泄露直至
        # EMFILE。宿主为单进程架构，改回进程内引擎（setdefault 保留环境变量覆盖）。
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
        _patch_ladybug_concurrency()
        _patch_ladybug_wal_recovery()
        return self._module

    async def _configure(self, module: Any) -> None:
        if self._configured:
            return
        root = Path(self.config.absolute_data_root)
        root.mkdir(parents=True, exist_ok=True)
        module.config.system_root_directory(str(root / "system"))
        module.config.data_root_directory(str(root / "data"))

        try:
            from agent.llm import get_llm_manager

            manager = get_llm_manager()
            chat_payload = resolve_chat_llm_config(self.config.chat, manager)
            anthropic_env_bridge(chat_payload)
            module.config.set_llm_config(chat_payload)
            self._resolved_chat = summarize_resolved(chat_payload, kind="chat")

            embedding_payload = resolve_embedding_llm_config(self.config.embedding, manager)
            if embedding_payload:
                module.config.set_embedding_config(embedding_payload)
                self._sanitize_embedding_dimensions()
            self._resolved_embedding = summarize_resolved(embedding_payload, kind="embedding")
        except Exception as exc:
            raise RuntimeError(f"无法映射 AnelfAgent 模型配置: {exc}") from exc
        self._configured = True

    def _sanitize_embedding_dimensions(self) -> None:
        """为 cognee 的 embedding 调用启用 litellm drop_params。

        cognee 对未知模型推导维度失败时回落 3072 并透传给 litellm，而 litellm 对
        非 "text-embedding-3" 命名的模型（DashScope 的 text-embedding-v3/v4 等
        OpenAI 兼容端点）本地拒绝 dimensions 参数（UnsupportedParamsError，
        经 litellm 重试放大为 30s 连接超时）。开启 drop_params 后该参数被自动
        丢弃，请求成功；cognee 连接测试随后探测真实维度并写回配置与引擎。
        对主 LLM 路径同样是容错增强（端点不支持的参数自动丢弃而非 400）。
        """
        try:
            import litellm
            litellm.drop_params = True
        except Exception as exc:
            log(f"litellm drop_params 设置失败（不影响主流程）: {exc}", "DEBUG", tag="记忆")

    async def _call(
        self,
        dotted_name: str,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        if self._module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        target = self._module
        for part in dotted_name.split("."):
            target = getattr(target, part)
        result = target(*args, **kwargs)
        if hasattr(result, "__await__"):
            limit = timeout if timeout is not None else self.config.timeout_seconds
            try:
                return await asyncio.wait_for(result, timeout=limit)
            except asyncio.TimeoutError:
                # 裸 TimeoutError 的 str() 为空，必须带操作上下文否则无法定位
                raise RuntimeError(
                    f"Cognee 调用 {dotted_name} 超时（>{limit:.0f}s）"
                ) from None
        return result

    # v2 memory-oriented API
    async def remember(self, data: Any, **kwargs: Any) -> Any:
        return await self._call("remember", data, **kwargs)

    async def recall(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        raw = await self._call("recall", query_text, **kwargs)
        return _normalize_recall(raw)

    async def improve(self, dataset: str = "main_dataset", **kwargs: Any) -> Any:
        return await self._call(
            "improve",
            dataset=dataset,
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def forget(self, **kwargs: Any) -> Any:
        return await self._call("forget", **kwargs)

    async def serve(self, **kwargs: Any) -> Any:
        return await self._call("serve", **kwargs)

    async def disconnect(self) -> Any:
        return await self._call("disconnect")

    async def push(self, **kwargs: Any) -> Any:
        return await self._call("push", **kwargs)

    async def export(self, **kwargs: Any) -> Any:
        return await self._call("export", **kwargs)

    # v1/lower-level public API
    async def add(self, data: Any, **kwargs: Any) -> Any:
        return await self._call(
            "add",
            data,
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def make_data_item(
        self,
        data: str,
        *,
        label: str,
        external_metadata: dict[str, Any],
    ) -> Any:
        """构造 Cognee 文档化公开输入类型 DataItem。"""
        if self._module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        data_item_module = importlib.import_module("cognee.tasks.ingestion.data_item")
        return data_item_module.DataItem(
            data,
            label=label,
            external_metadata=external_metadata,
        )

    async def cognify(self, **kwargs: Any) -> Any:
        return await self._call(
            "cognify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def search(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        raw = await self._call("search", query_text, **kwargs)
        return _normalize_recall(raw)

    async def memify(self, **kwargs: Any) -> Any:
        return await self._call(
            "memify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def update(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("update", *args, **kwargs)

    async def run_custom_pipeline(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("run_custom_pipeline", *args, **kwargs)

    async def run_migrations(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("run_migrations", *args, **kwargs)

    # Dataset namespace
    async def list_datasets(self, **kwargs: Any) -> Any:
        return await self._call("datasets.list_datasets", **kwargs)

    async def discover_datasets(self, directory_path: str) -> Any:
        return await self._call("datasets.discover_datasets", directory_path)

    async def list_data(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._call("datasets.list_data", _to_uuid(dataset_id), **kwargs)

    async def has_data(self, dataset_id: Any, **kwargs: Any) -> bool:
        return bool(await self._call("datasets.has_data", _to_uuid(dataset_id), **kwargs))

    async def get_dataset_status(self, dataset_ids: list[Any], **kwargs: Any) -> Any:
        return await self._call(
            "datasets.get_status",
            [_to_uuid(dataset_id) for dataset_id in dataset_ids],
            **kwargs,
        )

    async def empty_dataset(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._call("datasets.empty_dataset", _to_uuid(dataset_id), **kwargs)

    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        return await self._call(
            "datasets.delete_data",
            _to_uuid(dataset_id),
            _to_uuid(data_id),
            **kwargs,
        )

    async def delete_all(self, **kwargs: Any) -> Any:
        return await self._call("datasets.delete_all", **kwargs)

    # Maintenance / diagnostics / visualization
    async def prune_data(self) -> Any:
        return await self._call("prune.prune_data")

    async def prune_system(self, **kwargs: Any) -> Any:
        return await self._call("prune.prune_system", **kwargs)

    async def visualize(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize", *args, **kwargs)

    async def visualize_graph(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize_graph", *args, **kwargs)

    async def get_schema_inventory(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("get_schema_inventory", *args, **kwargs)

    async def get_memory_provenance_graph(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("get_memory_provenance_graph", *args, **kwargs)

    async def visualize_memory_provenance(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize_memory_provenance", *args, **kwargs)

    async def enable_tracing(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("enable_tracing", *args, **kwargs)

    async def disable_tracing(self) -> Any:
        return await self._call("disable_tracing")

    async def get_last_trace(self) -> Any:
        return await self._call("get_last_trace")

    async def get_all_traces(self) -> Any:
        return await self._call("get_all_traces")

    async def clear_traces(self) -> Any:
        return await self._call("clear_traces")

    def search_type(self, name: str) -> Any:
        if self._module is None:
            raise RuntimeError("Cognee 未初始化")
        return getattr(self._module.SearchType, name.upper())

    def public_namespace(self, name: str) -> Any:
        """获取 agents/session/migration/agent_memory 等公开命名空间。"""
        if self._module is None or not self._configured:
            raise RuntimeError("Cognee 未初始化")
        if name not in {"agents", "session", "migration", "agent_memory", "config", "pipelines", "Drop"}:
            raise ValueError(f"不允许访问未承诺的 Cognee 命名空间: {name}")
        return getattr(self._module, name)


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


def _quieten_cognee_logger() -> None:
    """cognee 的 setup_logging() 接管了 stdlib root logger，限制其输出级别。"""
    import logging as _logging

    root = _logging.getLogger()
    for handler in root.handlers:
        if handler.level < _logging.WARNING:
            handler.setLevel(_logging.WARNING)
    # cognee 子 logger 也限制
    _logging.getLogger("cognee").setLevel(_logging.WARNING)


def _patch_ladybug_concurrency() -> None:
    """给 LadybugAdapter.query 包进程级串行锁（防 pybind 层段错误）。

    ladybug/Kuzu 的 pybind connection 非线程安全，而 cognee 的非 shared-lock
    查询路径（ladybug/adapter.py 非 shared_ladybug_lock 分支）明确让多个查询
    在同一 connection 上并发 execute（"runs unlocked so multiple queries can
    execute concurrently"），图谱抽取 pipeline 并发任务下在 macOS arm64 直接
    Fatal Python error: Segmentation fault（进程崩溃，无法捕获）。
    shared_ladybug_lock 依赖 Redis（本地部署不具备），故在入口处统一串行化——
    图查询不是吞吐瓶颈，串行代价可接受，换进程不崩溃。
    幂等：重复导入只包一次。
    """
    try:
        from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter
    except Exception as exc:
        log(f"ladybug 并发补丁跳过（不影响主流程）: {exc}", "DEBUG", tag="记忆")
        return
    if getattr(LadybugAdapter.query, "_anel_serialized", False):
        return
    import functools

    lock = asyncio.Lock()
    original = LadybugAdapter.query

    @functools.wraps(original)
    async def serialized_query(self: Any, query: str, params: Any = None) -> Any:
        async with lock:
            return await original(self, query, params)

    serialized_query._anel_serialized = True  # type: ignore[attr-defined]
    LadybugAdapter.query = serialized_query
    log("ladybug 查询已串行化（防 pybind 并发段错误）", "DEBUG", tag="记忆")


def _patch_ladybug_wal_recovery() -> None:
    """让 cognee 以容错模式打开 ladybug 库（WAL 损坏时恢复而非抛错）。

    checkpoint 进行到一半进程被杀时，冻结 WAL（.wal.checkpoint）尾部留下半条
    记录，打开回放读到非法记录类型触发 wal_record.cpp 的 UNREACHABLE_CODE
    断言。cognee 自带兜底只删活动 WAL（<db>.wal），覆盖不到冻结 WAL，该
    数据集图库从此永久无法打开。throw_on_wal_replay_failure=False 是 ladybug
    官方恢复语义：回放到损坏点前最后一个已提交事务，仅丢失被中断的事务，
    比 cognee 删整个 WAL 的兜底损失更小；WAL 完好时行为完全不变。
    幂等：重复导入只包一次。
    """
    try:
        from cognee.infrastructure.databases.graph.ladybug import adapter as ladybug_adapter
    except Exception as exc:
        log(f"ladybug WAL 恢复补丁跳过（不影响主流程）: {exc}", "DEBUG", tag="记忆")
        return
    original = ladybug_adapter.Database
    if getattr(original, "_anel_wal_tolerant", False):
        return

    class _WalTolerantDatabase(original):  # type: ignore[valid-type, misc]
        """默认容错回放的 Database 包装（保留显式传参覆盖）。"""

        _anel_wal_tolerant = True

        def __init__(self, database_path: Any = None, **kwargs: Any) -> None:
            kwargs.setdefault("throw_on_wal_replay_failure", False)
            super().__init__(database_path, **kwargs)

    ladybug_adapter.Database = _WalTolerantDatabase
    log("ladybug 已启用 WAL 容错恢复（损坏时回放到最后提交点）", "DEBUG", tag="记忆")


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
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        raw_id = (
            metadata.get("chunk_id")
            or data.get("id")
            or data.get("qa_id")
            or f"result-{index}"
        )
        score_value = data.get("score", 0.0)
        score = float(score_value) if isinstance(score_value, (int, float)) else 0.0
        normalized.append(CogneeRecallItem(
            id=f"cognee:{raw_id}",
            content=content,
            score=score,
            source="cognee_chunk" if "chunk" in source_value.lower() or metadata.get("chunk_id") else "cognee_graph",
            dataset_id=str(data.get("dataset_id", "")),
            dataset_name=str(data.get("dataset_name", "")),
            metadata=metadata,
            raw=data,
        ))
    return normalized
